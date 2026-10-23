"""A small vector-store interface for the guidelines RAG, with a Chroma implementation.

Kept narrow (upsert, delete, hashes, query, reset) so the backing store can move to pgvector later.
Metadata values are scalars (str, int, float, bool), as both Chroma and a SQL table can hold them.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

Metadata = dict[str, str | int | float | bool]
COLLECTION = "guidelines"


@dataclass
class Hit:
    id: str
    text: str
    metadata: Metadata
    similarity: float  # cosine similarity, -1..1 (higher is closer)


class VectorStore(Protocol):
    def upsert(self, ids: list[str], embeddings: list[list[float]], texts: list[str],
               metadatas: list[Metadata]) -> None: ...

    def delete(self, ids: list[str]) -> None: ...

    def hashes(self) -> dict[str, str]:
        """Chunk id -> the content hash stored with it."""
        ...

    def query(self, embedding: list[float], k: int, where: dict[str, Any] | None = None) -> list[Hit]: ...

    def reset(self) -> None: ...


class ChromaStore:
    """Chroma, persisted on disk, cosine distance."""

    def __init__(self, path: str | Path, collection: str = COLLECTION) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        Path(path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path),
                                                 settings=ChromaSettings(anonymized_telemetry=False))
        self._name = collection
        self._collection = self._open()

    def _open(self):
        return self._client.get_or_create_collection(self._name, metadata={"hnsw:space": "cosine"})

    def upsert(self, ids, embeddings, texts, metadatas) -> None:
        if ids:
            self._collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._collection.delete(ids=ids)

    def hashes(self) -> dict[str, str]:
        got = self._collection.get(include=["metadatas"])
        return {i: str((m or {}).get("hash", "")) for i, m in zip(got["ids"], got["metadatas"])}

    def query(self, embedding: list[float], k: int, where: dict[str, Any] | None = None) -> list[Hit]:
        count = self._collection.count()
        if not count:
            return []
        got = self._collection.query(query_embeddings=[embedding], n_results=min(k, count), where=where or None,
                                     include=["documents", "metadatas", "distances"])
        return [Hit(id=i, text=t, metadata=m or {}, similarity=1.0 - d)
                for i, t, m, d in zip(got["ids"][0], got["documents"][0], got["metadatas"][0], got["distances"][0])]

    def reset(self) -> None:
        self._client.delete_collection(self._name)
        self._collection = self._open()
