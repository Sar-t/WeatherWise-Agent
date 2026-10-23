"""Retrieve official guideline snippets (CPCB, MoHFW, NDMA, WHO) for a situation.

Filter by metadata first (topics, activity, audience), then rank by semantic similarity, then
clean: drop weak matches, keep one chunk per document, and fit a token budget. Returns [] when
nothing relevant passes; never raises just because nothing matched.
"""

import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any

MIN_SIMILARITY = 0.80  # cosine; calibrated on evals/retrieval_cases.yaml with multilingual-e5-small
TOKEN_BUDGET = 1200  # total for the returned texts
CANDIDATES_PER_RESULT = 3  # fetch k * this before cleaning
TOKENS_PER_WORD = 1.3


@dataclass
class Guideline:
    doc_id: str
    title: str
    text: str
    source_name: str
    source_url: str
    topics: list[str] = field(default_factory=list)
    score: float = 0.0  # cosine similarity to the query
    severity: str = ""  # the document's severity tags, "NAQI Poor | NAQI Very Poor"


def estimate_tokens(text: str) -> int:
    """Rough token count for English/Hinglish prose (no tokenizer dependency)."""
    return math.ceil(len(text.split()) * TOKENS_PER_WORD)


# --- shared model and store (lazy, loaded once) ------------------------------------------

_lock = threading.Lock()
_store: Any = None
_embedder: Any = None


def configure(store: Any = None, embedder: Any = None) -> None:
    """Use this store and embedder instead of the configured ones (tests); None resets to lazy."""
    global _store, _embedder
    with _lock:
        _store, _embedder = store, embedder


def _resources() -> tuple[Any, Any]:
    global _store, _embedder
    with _lock:
        if _store is None or _embedder is None:
            from backend.core.config import get_settings
            from backend.services.embeddings import SentenceTransformerEmbedder
            from backend.services.vector_store import ChromaStore

            settings = get_settings()
            _store = _store or ChromaStore(settings.vector_store_dir)
            _embedder = _embedder or SentenceTransformerEmbedder(settings.embedding_model)
        return _store, _embedder


def warm_up() -> float:
    """Load the embedding model and store now (e.g. at startup) instead of on the first question.
    Returns the seconds it took."""
    import time

    started = time.perf_counter()
    _, embedder = _resources()
    embedder.embed_query("warm up")
    return time.perf_counter() - started


# --- retrieval -------------------------------------------------------------------------------


def build_query(topics: list[str] | None = None, activity: str | None = None, severity: str | None = None,
                query_text: str | None = None) -> str:
    """'running; NAQI 230, Poor; air quality' from the structured inputs (plus free text if given)."""
    parts = []
    if activity:
        parts.append(activity.replace("_", " "))
    if severity:
        parts.append(severity)
    if topics:
        parts.append(", ".join(t.replace("_", " ") for t in topics))
    if query_text:
        parts.append(query_text)
    return "; ".join(parts)


def build_filter(topics: list[str] | None = None, activity: str | None = None,
                 audience: str | list[str] | None = None) -> dict[str, Any]:
    """Metadata filter: any of the topics; the activity or 'all'; general plus the requested audience."""
    clauses = []
    if topics:
        clauses.append(_any([{f"topic_{t}": True} for t in topics]))
    if activity:
        clauses.append(_any([{"act_all": True}, {f"act_{activity}": True}]))
    requested = [audience] if isinstance(audience, str) else list(audience or [])
    audiences = list(dict.fromkeys(["general", *requested]))
    clauses.append(_any([{f"aud_{a}": True} for a in audiences]))
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower())}


def severity_match(requested: str | None, doc_severity: str) -> int:
    """How specifically a document's severity tags match the request: the word count of the longest
    tag whose words all appear in it ("NAQI Moderate" in "NAQI 150, Moderate" -> 2); 0 if none."""
    if not requested:
        return 0
    wanted = _words(requested)
    tags = [_words(tag) for tag in doc_severity.split("|")]
    return max((len(tag) for tag in tags if tag and tag <= wanted), default=0)


def _any(conditions: list[dict]) -> dict:
    return conditions[0] if len(conditions) == 1 else {"$or": conditions}


def retrieve_guidelines(
    topics: list[str] | None = None,
    activity: str | None = None,
    audience: str | list[str] | None = None,
    severity: str | None = None,
    query_text: str | None = None,
    k: int = 4,
) -> list[Guideline]:
    """Up to `k` relevant guideline snippets, one per document, best first, within TOKEN_BUDGET."""
    query = build_query(topics, activity, severity, query_text)
    if not query:
        return []
    store, embedder = _resources()
    hits = store.query(embedder.embed_query(query), k * CANDIDATES_PER_RESULT,
                       build_filter(topics, activity, audience))

    # Documents whose `severity` matches the requested one come first (most specific match first),
    # then by similarity: the NAQI band documents read alike to the model, their tags don't.
    ranked = sorted(hits, key=lambda h: (-severity_match(severity, str(h.metadata.get("severity", ""))),
                                         -h.similarity))
    results, seen, used = [], set(), 0
    for hit in ranked:
        doc_id = str(hit.metadata.get("doc_id", hit.id))
        if hit.similarity < MIN_SIMILARITY or doc_id in seen:
            continue
        cost = estimate_tokens(hit.text)
        if used + cost > TOKEN_BUDGET:
            continue  # a shorter later hit may still fit
        seen.add(doc_id)
        used += cost
        results.append(Guideline(
            doc_id=doc_id, title=str(hit.metadata.get("title", "")), text=hit.text,
            source_name=str(hit.metadata.get("source_name", "")), source_url=str(hit.metadata.get("source_url", "")),
            topics=[t for t in str(hit.metadata.get("topics", "")).split(",") if t],
            score=round(hit.similarity, 4), severity=str(hit.metadata.get("severity", "")),
        ))
        if len(results) == k:
            break
    return results
