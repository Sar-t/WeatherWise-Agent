"""Guidelines RAG: front matter, chunking, idempotent indexing, filtering and cleaning.

A fake embedder (hashed bag of words: shared words -> similar vectors) and a temporary Chroma store
keep these fast and offline. One integration test uses the real model: RUN_SLOW=1 pytest -m slow.
"""

import hashlib
import math
import os
import re
import shutil
from pathlib import Path

import pytest

from backend.services import guidelines
from backend.services.guidelines import build_filter, build_query, retrieve_guidelines
from backend.services.vector_store import ChromaStore
from ingestion.build_index import (
    DOCS_DIR,
    SINGLE_CHUNK_MAX,
    TARGET_TOKENS,
    GuidelineError,
    build_index,
    chunk_document,
    load_documents,
    parse_document,
    tokens,
)

DIM = 256


class FakeEmbedder:
    """Deterministic: each word hashes to a dimension; vectors are normalised word counts."""

    name = "fake-bag-of-words"

    def __init__(self) -> None:
        self.embedded = 0

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def write_doc(folder: Path, doc_id: str, body: str | None = None, **front) -> Path:
    fields = {"id": doc_id, "title": f"Title of {doc_id}", "topics": "[heat]", "activities": "all",
              "audience": "[general]", "severity": "[]", "source_name": "WHO",
              "source_url": "https://www.who.int/x", "retrieved": "2026-10-23"} | front
    head = "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)
    body = body or "# Heading\n\n" + "Drink water often and stay in the shade during the hottest hours. " * 4
    path = folder / f"{doc_id}.md"
    path.write_text(f"---\n{head}\n---\n\n{body}\n", encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path: Path) -> ChromaStore:
    return ChromaStore(tmp_path / "store")


@pytest.fixture
def indexed(store: ChromaStore, monkeypatch: pytest.MonkeyPatch):
    """The real knowledge base, indexed with the fake embedder, behind retrieve_guidelines()."""
    embedder = FakeEmbedder()
    build_index(store, embedder, DOCS_DIR)
    guidelines.configure(store, embedder)
    monkeypatch.setattr(guidelines, "MIN_SIMILARITY", 0.05)  # bag-of-words similarities are low
    yield store
    guidelines.configure(None, None)


# --- front matter --------------------------------------------------------------------


def test_shipped_documents_are_valid() -> None:
    docs = load_documents()
    assert len(docs) >= 15 and len({d.id for d in docs}) == len(docs)
    assert all(d.source_url.startswith("https://") for d in docs)


@pytest.mark.parametrize("front, message", [
    ({"title": None}, "title"),
    ({"topics": "[weather]"}, "topics"),
    ({"topics": "[]"}, "at least one"),
    ({"audience": "[everyone]"}, "audience"),
    ({"activities": "[kite_flying]"}, "activities must be 'all' or scoring profiles"),
    ({"source_url": "http://insecure.example"}, "https://"),
    ({"retrieved": "yesterday"}, "retrieved"),
    ({"colour": "blue"}, "colour"),
])
def test_bad_front_matter_fails_loudly(tmp_path: Path, front: dict, message: str) -> None:
    path = write_doc(tmp_path, "bad-doc", **front)
    with pytest.raises(GuidelineError, match="bad-doc.md") as exc:
        parse_document(path)
    assert message in str(exc.value)


def test_missing_front_matter_wrong_id_and_empty_body_fail(tmp_path: Path) -> None:
    (tmp_path / "plain.md").write_text("# Just markdown\n\nNo front matter here.", encoding="utf-8")
    with pytest.raises(GuidelineError, match="no YAML front matter"):
        parse_document(tmp_path / "plain.md")
    with pytest.raises(GuidelineError, match="must match the file name"):
        parse_document(write_doc(tmp_path, "file-name", id="other-id"))
    with pytest.raises(GuidelineError, match="body is missing or too short"):
        parse_document(write_doc(tmp_path, "empty", body="# Title\n\nToo short."))
    with pytest.raises(GuidelineError, match="no guideline documents"):
        load_documents(tmp_path / "nowhere")


# --- chunking ------------------------------------------------------------------------


def test_short_document_is_one_chunk_led_by_title_and_aliases() -> None:
    doc = parse_document(DOCS_DIR / "naqi-moderately-polluted.md")
    [chunk] = chunk_document(doc)
    assert chunk.id == "naqi-moderately-polluted#0"
    assert chunk.text.startswith("NAQI Moderately Polluted (101-200): who should take care "
                                 "(also: Moderate, Moderately Polluted)")
    assert "# NAQI" not in chunk.text  # the H1 is replaced by the title line
    assert chunk.metadata["topic_air_quality"] and chunk.metadata["act_all"] and chunk.metadata["aud_children"]


def test_long_document_is_split_at_headings_with_overlap(tmp_path: Path) -> None:
    sections = [f"## Part {i}\n\n" + " ".join(f"word{i}x{j}" for j in range(120)) + f"\nLast line of part {i}."
                for i in range(5)]
    doc = parse_document(write_doc(tmp_path, "long-doc", body="# Long\n\n" + "\n\n".join(sections)))
    chunks = chunk_document(doc)
    assert tokens(doc.body) > SINGLE_CHUNK_MAX and len(chunks) > 1
    assert [c.id for c in chunks] == [f"long-doc#{i}" for i in range(len(chunks))]
    for previous, chunk in zip(chunks, chunks[1:]):
        assert chunk.text.split("\n\n")[1] == previous.text.splitlines()[-1]  # small overlap
    assert all(tokens(c.text) <= TARGET_TOKENS + 200 for c in chunks)


# --- indexing ------------------------------------------------------------------------


def test_indexing_is_idempotent_and_tracks_changes(tmp_path: Path, store: ChromaStore) -> None:
    docs = tmp_path / "docs"
    shutil.copytree(DOCS_DIR, docs)
    embedder = FakeEmbedder()

    first = build_index(store, embedder, docs)
    total = first.embedded
    assert total >= 16 and (first.unchanged, first.deleted) == (0, 0)

    again = build_index(store, embedder, docs)
    assert (again.embedded, again.unchanged, again.deleted) == (0, total, 0)  # nothing re-embedded
    assert embedder.embedded == total

    path = docs / "uv-index-levels.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nOne more sentence.\n", encoding="utf-8")
    (docs / "naqi-severe.md").unlink()
    changed = build_index(store, embedder, docs)
    assert (changed.embedded, changed.deleted) == (1, 1)
    assert "naqi-severe#0" not in store.hashes()

    rebuilt = build_index(store, embedder, docs, rebuild=True)
    assert rebuilt.embedded == total - 1 and rebuilt.unchanged == 0


def test_a_different_embedding_model_re_embeds_everything(tmp_path: Path, store: ChromaStore) -> None:
    build_index(store, FakeEmbedder(), DOCS_DIR)
    other = FakeEmbedder()
    other.name = "another-model"
    assert build_index(store, other, DOCS_DIR).unchanged == 0


# --- query building and filtering ----------------------------------------------------


def test_query_text_from_structured_inputs() -> None:
    assert build_query(["air_quality"], "running", "NAQI 230, Poor") == "running; NAQI 230, Poor; air quality"
    assert build_query(query_text="garmi mein bahar jana safe hai?") == "garmi mein bahar jana safe hai?"
    assert build_query() == ""


def test_filter_shape() -> None:
    assert build_filter(["heat"], "running", "children") == {"$and": [
        {"topic_heat": True},
        {"$or": [{"act_all": True}, {"act_running": True}]},
        {"$or": [{"aud_general": True}, {"aud_children": True}]},
    ]}
    assert build_filter() == {"aud_general": True}


def test_metadata_filter_keeps_only_matching_topics(indexed) -> None:
    results = retrieve_guidelines(topics=["cold"], query_text="what to wear, shivering, heaters")
    assert results and all("cold" in g.topics for g in results)


def test_activity_filter_excludes_documents_for_other_activities(indexed) -> None:
    # exercise-in-polluted-air lists running, walking, cycling, football, hiking, general_outdoor
    running = retrieve_guidelines(topics=["air_quality"], activity="running", query_text="exercise running walk")
    travel = retrieve_guidelines(topics=["air_quality"], activity="travel", query_text="exercise running walk")
    assert "exercise-in-polluted-air" in [g.doc_id for g in running]
    assert "exercise-in-polluted-air" not in [g.doc_id for g in travel]


def test_audience_filter_adds_to_general(indexed, store) -> None:
    # all shipped docs include "general" except the masks/vulnerable-groups one
    plain = retrieve_guidelines(topics=["air_quality"], query_text="mask N95 vulnerable groups", k=8)
    sensitive = retrieve_guidelines(topics=["air_quality"], audience="sensitive_groups",
                                    query_text="mask N95 vulnerable groups", k=8)
    assert "air-pollution-masks-and-vulnerable-groups" not in [g.doc_id for g in plain]
    assert "air-pollution-masks-and-vulnerable-groups" in [g.doc_id for g in sensitive]


# --- cleaning ------------------------------------------------------------------------


def test_results_carry_source_and_score(indexed) -> None:
    [top, *_] = retrieve_guidelines(topics=["thunderstorm"], query_text="lightning shelter thunder outdoors")
    assert top.doc_id.startswith("thunderstorm-lightning")
    assert top.source_name.startswith("NDMA") and top.source_url.startswith("https://")
    assert 0 < top.score <= 1 and top.text.startswith(top.title)


def test_threshold_drops_weak_matches(indexed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guidelines, "MIN_SIMILARITY", 0.99)
    assert retrieve_guidelines(topics=["heat"], query_text="hot day") == []


def test_one_chunk_per_document(tmp_path: Path, store: ChromaStore, monkeypatch: pytest.MonkeyPatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    sections = [f"## Heat part {i}\n\n" + "heat water shade " * 100 for i in range(4)]
    write_doc(docs, "heat-long", body="# Heat\n\n" + "\n\n".join(sections))
    write_doc(docs, "heat-short")
    embedder = FakeEmbedder()
    build_index(store, embedder, docs)
    guidelines.configure(store, embedder)
    monkeypatch.setattr(guidelines, "MIN_SIMILARITY", 0.0)
    try:
        results = retrieve_guidelines(topics=["heat"], query_text="heat water shade", k=4)
    finally:
        guidelines.configure(None, None)
    assert sorted(g.doc_id for g in results) == ["heat-long", "heat-short"]


def test_token_budget_limits_the_total(indexed, monkeypatch: pytest.MonkeyPatch) -> None:
    wide = retrieve_guidelines(topics=["air_quality"], query_text="air pollution NAQI health", k=8)
    monkeypatch.setattr(guidelines, "TOKEN_BUDGET", 300)
    tight = retrieve_guidelines(topics=["air_quality"], query_text="air pollution NAQI health", k=8)
    assert len(tight) < len(wide)
    assert sum(guidelines.estimate_tokens(g.text) for g in tight) <= 300


def test_nothing_relevant_returns_empty_not_an_error(indexed) -> None:
    assert retrieve_guidelines(topics=["fog"], query_text="dense fog driving") == []  # no fog documents yet
    assert retrieve_guidelines() == []


# --- real model (slow, opt-in) ---------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="downloads the real embedding model; RUN_SLOW=1")
def test_real_model_finds_the_naqi_poor_document(tmp_path: Path) -> None:
    from backend.core.config import get_settings
    from backend.services.embeddings import SentenceTransformerEmbedder

    store = ChromaStore(tmp_path / "real")
    embedder = SentenceTransformerEmbedder(get_settings().embedding_model)
    build_index(store, embedder, DOCS_DIR)
    guidelines.configure(store, embedder)
    try:
        results = retrieve_guidelines(topics=["air_quality"], activity="running", severity="NAQI 230, Poor")
    finally:
        guidelines.configure(None, None)
    assert results and results[0].doc_id in {"naqi-poor", "exercise-in-polluted-air"}
