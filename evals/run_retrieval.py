"""Retrieval evals against the real local embedding model (no LLM, no quota).

Usage (from the repo root, venv active):
    python evals/run_retrieval.py                  # syncs the index, then prints hit@1 / hit@4
    python evals/run_retrieval.py --threshold 0    # show raw scores, e.g. to calibrate MIN_SIMILARITY
"""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.config import get_settings  # noqa: E402
from backend.services import guidelines  # noqa: E402
from backend.services.embeddings import SentenceTransformerEmbedder  # noqa: E402
from backend.services.vector_store import ChromaStore  # noqa: E402
from ingestion.build_index import build_index  # noqa: E402

CASES = Path(__file__).parent / "retrieval_cases.yaml"


def main(threshold: float | None, only: set[str]) -> None:
    settings = get_settings()
    store, embedder = ChromaStore(settings.vector_store_dir), SentenceTransformerEmbedder(settings.embedding_model)
    stats = build_index(store, embedder)  # idempotent: only changed chunks are embedded
    guidelines.configure(store, embedder)
    if threshold is not None:
        guidelines.MIN_SIMILARITY = threshold
    print(f"Model: {settings.embedding_model}  index: {stats.embedded} embedded, {stats.unchanged} unchanged  "
          f"threshold: {guidelines.MIN_SIMILARITY}\n")

    cases = [c for c in yaml.safe_load(CASES.read_text(encoding="utf-8")) if not only or c["id"] in only]
    hit1 = hit4 = 0
    for case in cases:
        results = guidelines.retrieve_guidelines(**case["input"], k=4)
        ids = [g.doc_id for g in results]
        expected = case["expected"]
        if expected:
            h1, h4 = bool(ids) and ids[0] in expected, any(i in expected for i in ids)
        else:  # nothing relevant exists: success is an empty result
            h1 = h4 = not ids
        hit1 += h1
        hit4 += h4
        mark = "ok  " if h1 else ("top4" if h4 else "MISS")
        shown = ", ".join(f"{g.doc_id} {g.score:.3f}" for g in results) or "(none)"
        print(f"{mark} {case['id']:<24} -> {shown}")
    n = len(cases)
    print(f"\nhit@1: {hit1}/{n} ({hit1 / n:.0%})   hit@4: {hit4}/{n} ({hit4 / n:.0%})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threshold", type=float, default=None, help="override MIN_SIMILARITY")
    parser.add_argument("--only", default="", help="comma-separated case ids")
    args = parser.parse_args()
    main(args.threshold, {x for x in args.only.split(",") if x})
