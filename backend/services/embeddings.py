"""Text embeddings for the guidelines RAG. Local sentence-transformers model, loaded once."""

from typing import Protocol


class Embedder(Protocol):
    name: str  # identifies the vectors (part of each chunk's content hash)

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """A sentence-transformers model with normalised vectors (cosine = dot product).

    E5 models expect "passage: " / "query: " prefixes; they're added when the name says e5.
    The model is downloaded on first use (~470 MB for multilingual-e5-small).
    """

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import: only when used

        self.name = model_name
        self._model = SentenceTransformer(model_name)
        self._e5 = "e5" in model_name.lower()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        texts = [f"passage: {t}" for t in texts] if self._e5 else texts
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        text = f"query: {text}" if self._e5 else text
        return self._model.encode([text], normalize_embeddings=True)[0].tolist()
