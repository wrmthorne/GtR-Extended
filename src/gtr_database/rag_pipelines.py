import numpy as np
import os
from dataclasses import dataclass
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer, CrossEncoder


@dataclass
class Chunk:
    text: str
    path: tuple[str, ...]
    score: float = 0.0


class RAGPipeline:
    def __init__(
        self,
        embed_model: str = "all-MiniLM-L6-v2",
        rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        alpha: float = 0.5,
        use_reranker: bool = True,
    ):
        self.embedder = SentenceTransformer(embed_model, trust_remote_code=True, token=os.environ.get("HF_TOKEN"))
        self.reranker = CrossEncoder(rerank_model, trust_remote_code=True, token=os.environ.get("HF_TOKEN")) if use_reranker else None
        self.alpha = alpha
        self.use_reranker = use_reranker
        self.chunks: list[Chunk] = []
        self._bm25: BM25Okapi | None = None
        self._dense_matrix: np.ndarray | None = None

    def index(self, opportunity) -> None:
        """Build ephemeral index from a ParsedOpportunity."""
        self.chunks = []
        if opportunity.metadata:
            self.chunks.append(Chunk(text=opportunity.metadata, path=("__metadata__",)))
        if opportunity.summary:
            self.chunks.append(Chunk(text=opportunity.summary, path=("__summary__",)))
        for section in opportunity.sections:
            self._walk(section, path=())
        self._build_index_from_chunks()

    def _build_index_from_chunks(self) -> None:
        if not self.chunks:
            self._bm25 = None
            self._dense_matrix = None
            return
        tokenized = [c.text.lower().split() for c in self.chunks]
        self._bm25 = BM25Okapi(tokenized)
        self._dense_matrix = self.embedder.encode(
            [c.text for c in self.chunks], normalize_embeddings=True
        )

    def _walk(self, section, path: tuple[str, ...]) -> None:
        current_path = path + (section.header,)
        # collect direct string content (not child sections)
        direct_text = []
        if path:  # nested section — prepend breadcrumb for context
            direct_text.append("> " + " > ".join(path + (section.header,)))
        if section.header:
            direct_text.append("#" * section.level + " " + section.header)
        for item in section.content:
            if isinstance(item, str):
                direct_text.append(item)
        if direct_text:
            self.chunks.append(Chunk(text="\n\n".join(direct_text), path=current_path))
        for item in section.content:
            if hasattr(item, "header"):  # Section
                self._walk(item, current_path)

    def query(
        self, questions: str | list[str], top_k: int = 10, final_k: int = 3, min_score: float = -float("inf"),
    ) -> str:
        if not self.chunks:
            return ""

        if isinstance(questions, str):
            questions = [questions]

        # Pinned chunks — always included regardless of retrieval score
        metadata_idx = next(
            (i for i, c in enumerate(self.chunks) if c.path == ("__metadata__",)), None
        )
        summary_idx = next(
            (i for i, c in enumerate(self.chunks) if c.path == ("__summary__",)), None
        )
        pinned = [i for i in [metadata_idx, summary_idx] if i is not None]

        # Union of top-k candidates across all query variants; accumulate hybrid scores
        candidate_set: set[int] = set()
        agg_hybrid = np.zeros(len(self.chunks))
        for q in questions:
            scores = self._hybrid_scores(q, top_k, self.alpha)
            agg_hybrid += scores
            candidate_set.update(int(i) for i in np.argsort(scores)[::-1][:top_k])
        candidate_indices = list(candidate_set)

        if self.use_reranker:
            pairs = [(questions[0], self.chunks[i].text) for i in candidate_indices]
            rerank_scores = self.reranker.predict(pairs)
            for idx, s in zip(candidate_indices, rerank_scores):
                self.chunks[idx].score = float(s)
        else:
            for idx in candidate_indices:
                self.chunks[idx].score = float(agg_hybrid[idx])

        # Filter by min_score, always keeping pinned chunks regardless of score
        pinned_set = set(pinned)
        ranked = [
            i for i in sorted(candidate_indices, key=lambda i: self.chunks[i].score, reverse=True)
            if self.chunks[i].score >= min_score or i in pinned_set
        ]

        # Retrieve final_k body chunks independently; pinned chunks are always added on top
        non_pinned = [i for i in ranked if i not in pinned_set]
        kept_body = self._branch_dedup(non_pinned)[:final_k]

        # Metadata first, then summary, then body chunks
        final_indices = pinned + kept_body
        passages = [self.chunks[i].text for i in final_indices]
        return "\n\n---\n\n".join(passages)

    def _hybrid_scores(self, question: str, top_k: int, alpha: float = 0.5) -> np.ndarray:
        tokens = question.lower().split()
        bm25_scores = self._bm25.get_scores(tokens)
        # normalize bm25 to [0, 1]
        bm25_max = bm25_scores.max()
        if bm25_max > 0:
            bm25_norm = bm25_scores / bm25_max
        else:
            bm25_norm = bm25_scores

        q_emb = self.embedder.encode([question], normalize_embeddings=True)
        dense_scores = (self._dense_matrix @ q_emb.T).squeeze()
        dense_norm = (dense_scores + 1) / 2  # normalise to [0, 1]

        return alpha * bm25_norm + (1 - alpha) * dense_norm

    def _branch_dedup(self, ranked_indices: list[int]) -> list[int]:
        """For any ancestor-descendant pair, keep only the higher scorer."""
        kept = []
        for i in ranked_indices:
            path_i = self.chunks[i].path
            redundant = False
            for j in kept:
                path_j = self.chunks[j].path
                if self._is_ancestor(path_i, path_j) or self._is_ancestor(path_j, path_i):
                    redundant = True
                    break
            if not redundant:
                kept.append(i)
        return kept

    @staticmethod
    def _is_ancestor(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
        """True if path a is a prefix of path b (a is ancestor of b)."""
        return len(a) < len(b) and b[: len(a)] == a

    def clear(self) -> None:
        self.chunks = []
        self._bm25 = None
        self._dense_matrix = None


class SimpleChunkingPipeline(RAGPipeline):
    """Flat sliding-window chunker for baseline comparison against hierarchical RAG.

    Splits the opportunity's full markdown into overlapping word-count windows.
    The same hybrid retrieval and reranking is applied as in RAGPipeline; the
    only difference is how the document is segmented.
    """

    def __init__(
        self,
        chunk_size: int = 200,
        chunk_overlap: int = 50,
        embed_model: str = "all-MiniLM-L6-v2",
        rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        alpha: float = 0.5,
        use_reranker: bool = True,
    ):
        super().__init__(embed_model=embed_model, rerank_model=rerank_model, alpha=alpha, use_reranker=use_reranker)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def index(self, opportunity) -> None:
        self.index_text(opportunity.markdown)

    def index_text(self, text: str) -> None:
        """Segment a plain text string into fixed-size sliding windows."""
        words = text.split()
        step = max(1, self.chunk_size - self.chunk_overlap)
        self.chunks = []
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + self.chunk_size])
            if chunk:
                self.chunks.append(Chunk(text=chunk, path=(str(i),)))
        self._build_index_from_chunks()