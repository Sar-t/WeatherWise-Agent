"""Build the guidelines vector index from knowledge/guidelines/*.md.

Usage (from the repo root, venv active):
    python -m ingestion.build_index            # add/update changed chunks, drop removed ones
    python -m ingestion.build_index --rebuild  # recreate the index from scratch

Each document's YAML front matter is validated (a bad file fails loudly, naming the file and
field). Documents are chunked by heading (target ~300 tokens, small overlap; short documents stay
one chunk), embedded locally and stored with their metadata. Every chunk carries a content hash,
so a re-run only embeds what changed.
"""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from backend.services.guidelines import estimate_tokens
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

DOCS_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "guidelines"
TARGET_TOKENS = 300
SINGLE_CHUNK_MAX = 375  # a document up to this many tokens stays one chunk

Topic = Literal["heat", "cold", "rain", "thunderstorm", "fog", "air_quality", "uv", "wind", "general"]
Audience = Literal["general", "sensitive_groups", "children", "older_adults", "outdoor_workers"]


class GuidelineError(ValueError):
    """A guideline document is missing, unreadable or invalid."""


class GuidelineDoc(BaseModel):
    """Front matter plus body of one guideline document."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    topics: list[Topic]
    activities: Literal["all"] | list[str]
    audience: list[Audience]
    severity: list[str]
    source_name: str
    source_url: str
    retrieved: date
    original_title: str | None = None
    aliases: list[str] = []
    body: str

    @field_validator("topics", "audience")
    @classmethod
    def _not_empty(cls, value: list) -> list:
        if not value:
            raise ValueError("must list at least one value")
        return value

    @field_validator("activities")
    @classmethod
    def _known_activities(cls, value: Literal["all"] | list[str]) -> Literal["all"] | list[str]:
        if value == "all":
            return value
        from backend.services.scoring.config import load_config

        known = set(load_config().profiles)
        if not value or (unknown := [a for a in value if a not in known]):
            raise ValueError(f"activities must be 'all' or scoring profiles {sorted(known)}")
        return value

    @field_validator("source_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("source_url must be an https:// URL")
        return value

    @model_validator(mode="after")
    def _has_body(self) -> "GuidelineDoc":
        if len(self.body.split()) < 20:
            raise ValueError("body is missing or too short")
        return self


@dataclass
class Chunk:
    id: str  # "<doc id>#<n>"
    text: str
    metadata: dict[str, str | int | float | bool]


# --- loading -----------------------------------------------------------------------


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)


def parse_document(path: Path) -> GuidelineDoc:
    text = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(text)
    if not match:
        raise GuidelineError(f"{path.name}: no YAML front matter (--- ... ---) at the top")
    try:
        front = yaml.safe_load(match.group(1)) or {}
        doc = GuidelineDoc(**front, body=match.group(2).strip())
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        raise GuidelineError(f"{path.name}: invalid front matter:\n{exc}") from exc
    if doc.id != path.stem:
        raise GuidelineError(f"{path.name}: id {doc.id!r} must match the file name")
    return doc


def load_documents(docs_dir: Path = DOCS_DIR) -> list[GuidelineDoc]:
    paths = sorted(Path(docs_dir).glob("*.md"))
    if not paths:
        raise GuidelineError(f"no guideline documents in {docs_dir}")
    return [parse_document(p) for p in paths]


# --- chunking ----------------------------------------------------------------------


def tokens(text: str) -> int:
    return estimate_tokens(text)


def _sections(body: str) -> list[str]:
    """The body split at '## ' headings (the '# Title' line is dropped; the title leads every chunk)."""
    lines = [line for line in body.splitlines() if not line.startswith("# ")]
    sections, current = [], []
    for line in lines:
        if line.startswith("## ") and current:
            sections.append("\n".join(current).strip())
            current = []
        current.append(line)
    sections.append("\n".join(current).strip())
    return [s for s in sections if s]


def chunk_document(doc: GuidelineDoc) -> list[Chunk]:
    """One chunk for a short document; else heading sections packed up to ~TARGET_TOKENS, each new
    chunk repeating the previous chunk's last line as a small overlap."""
    header = doc.title + (f" (also: {', '.join(doc.aliases)})" if doc.aliases else "")
    sections = _sections(doc.body)
    if tokens("\n\n".join(sections)) <= SINGLE_CHUNK_MAX:
        groups = ["\n\n".join(sections)]
    else:
        groups, current = [], []
        for section in sections:
            if current and tokens("\n\n".join(current + [section])) > TARGET_TOKENS:
                groups.append("\n\n".join(current))
                overlap = groups[-1].splitlines()[-1]
                current = [overlap] if not overlap.startswith("## ") else []
            current.append(section)
        groups.append("\n\n".join(current))
    return [Chunk(id=f"{doc.id}#{i}", text=f"{header}\n\n{group}", metadata=_metadata(doc, i))
            for i, group in enumerate(groups)]


def _metadata(doc: GuidelineDoc, index: int) -> dict[str, str | int | float | bool]:
    """Scalar metadata (Chroma/pgvector friendly): readable fields plus one flag per filter value."""
    activities = ["all"] if doc.activities == "all" else doc.activities
    meta: dict[str, str | int | float | bool] = {
        "doc_id": doc.id, "chunk": index, "title": doc.title, "source_name": doc.source_name,
        "source_url": doc.source_url, "topics": ",".join(doc.topics), "activities": ",".join(activities),
        "audience": ",".join(doc.audience), "severity": " | ".join(doc.severity),
    }
    meta |= {f"topic_{t}": True for t in doc.topics}
    meta |= {f"act_{a}": True for a in activities}
    meta |= {f"aud_{a}": True for a in doc.audience}
    return meta


def content_hash(chunk: Chunk, model_name: str) -> str:
    payload = json.dumps([model_name, chunk.text, chunk.metadata], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- indexing ----------------------------------------------------------------------


@dataclass
class BuildStats:
    embedded: int = 0  # new or changed chunks
    unchanged: int = 0
    deleted: int = 0


def build_index(store, embedder, docs_dir: Path = DOCS_DIR, rebuild: bool = False) -> BuildStats:
    """Sync the store with the documents: embed new/changed chunks, drop chunks that no longer exist."""
    chunks = [c for doc in load_documents(docs_dir) for c in chunk_document(doc)]
    if rebuild:
        store.reset()
    stored = store.hashes()
    stats = BuildStats()
    todo = []
    for chunk in chunks:
        chunk.metadata["hash"] = content_hash(chunk, embedder.name)
        if stored.get(chunk.id) == chunk.metadata["hash"]:
            stats.unchanged += 1
        else:
            todo.append(chunk)
    if todo:
        vectors = embedder.embed_documents([c.text for c in todo])
        store.upsert([c.id for c in todo], vectors, [c.text for c in todo], [c.metadata for c in todo])
        stats.embedded = len(todo)
    stale = sorted(set(stored) - {c.id for c in chunks})
    store.delete(stale)
    stats.deleted = len(stale)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rebuild", action="store_true", help="recreate the index from scratch")
    parser.add_argument("--docs", type=Path, default=DOCS_DIR, help="guideline documents folder")
    args = parser.parse_args(argv)

    from backend.core.config import get_settings
    from backend.services.embeddings import SentenceTransformerEmbedder
    from backend.services.vector_store import ChromaStore

    settings = get_settings()
    try:
        load_documents(args.docs)  # validate before loading the model
    except GuidelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Embedding model: {settings.embedding_model} (downloaded on first run)")
    stats = build_index(ChromaStore(settings.vector_store_dir), SentenceTransformerEmbedder(settings.embedding_model),
                        args.docs, rebuild=args.rebuild)
    print(f"Index at {settings.vector_store_dir}: {stats.embedded} embedded, {stats.unchanged} unchanged, "
          f"{stats.deleted} deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
