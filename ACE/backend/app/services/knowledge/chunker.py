import math
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import tiktoken

from ...utils.common.logger import Logger
from ...utils.errors import ValidationError

logger = Logger(__name__).get_logger()

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")


@dataclass(frozen=True)
class TextChunk:
    index: int
    content: str
    token_count: int
    embedding_text: str | None = None
    parent_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ChunkingStrategy(ABC):
    name: str = ""

    def __init__(
        self,
        *,
        max_chunk_size: int,
        chunk_overlap: int,
        min_chunk_size: int | None = None,
        target_chunks: int = 8,
        encoding_name: str = "cl100k_base",
    ) -> None:
        if max_chunk_size <= 0:
            raise ValueError("max_chunk_size must be positive.")
        if chunk_overlap < 0 or chunk_overlap >= max_chunk_size:
            raise ValueError("chunk_overlap must be in [0, max_chunk_size).")
        if target_chunks <= 0:
            raise ValueError("target_chunks must be positive.")
        self._max_chunk_size = max_chunk_size
        self._chunk_overlap = chunk_overlap
        self._min_chunk_size = max(
            32, min_chunk_size if min_chunk_size is not None else max_chunk_size // 8
        )
        if self._min_chunk_size > max_chunk_size:
            self._min_chunk_size = max_chunk_size
        self._target_chunks = target_chunks
        self._overlap_ratio = min(0.9, chunk_overlap / max_chunk_size)
        try:
            self._encoding = tiktoken.get_encoding(encoding_name)
        except Exception:
            self._encoding = tiktoken.get_encoding("cl100k_base")

    @abstractmethod
    async def split(self, text: str) -> list[TextChunk]:
        """Split raw text into ordered chunks."""

    def _count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def _effective_size(self, total_tokens: int) -> int:
        if total_tokens <= self._min_chunk_size:
            return total_tokens
        target = math.ceil(total_tokens / self._target_chunks)
        return max(self._min_chunk_size, min(target, self._max_chunk_size))

    def _window_tokens(self, text: str, *, chunk_size: int | None = None) -> list[str]:
        """Fixed token windows with overlap; returns decoded window texts."""
        tokens = self._encoding.encode(text)
        total = len(tokens)
        if total == 0:
            return []
        size = chunk_size or self._effective_size(total)
        overlap = min(int(size * self._overlap_ratio), size - 1) if size > 1 else 0
        step = max(1, size - overlap)

        windows: list[str] = []
        start = 0
        while start < total:
            window = tokens[start : start + size]
            content = self._encoding.decode(window).strip()
            if content:
                windows.append(content)
            if start + size >= total:
                break
            start += step
        return windows

    def _pack_segments(self, segments: list[str], *, chunk_size: int) -> list[str]:
        """Greedily pack text segments into chunks up to chunk_size tokens."""
        packed: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            seg_tokens = self._count_tokens(segment)
            if seg_tokens > chunk_size:
                if current:
                    packed.append("\n\n".join(current))
                    current, current_tokens = [], 0
                packed.extend(self._window_tokens(segment, chunk_size=chunk_size))
                continue
            if current_tokens + seg_tokens > chunk_size and current:
                packed.append("\n\n".join(current))
                current, current_tokens = [], 0
            current.append(segment)
            current_tokens += seg_tokens

        if current:
            packed.append("\n\n".join(current))
        return [chunk for chunk in packed if chunk.strip()]

    def _to_chunks(
        self,
        pieces: list[str],
        *,
        parent_index: int | None = None,
        embedding_prefix: str = "",
        metadata: dict[str, Any] | None = None,
        start_index: int = 0,
    ) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for offset, content in enumerate(pieces):
            embedding_text = (
                f"{embedding_prefix}\n\n{content}" if embedding_prefix else None
            )
            chunks.append(
                TextChunk(
                    index=start_index + offset,
                    content=content,
                    token_count=self._count_tokens(content),
                    embedding_text=embedding_text,
                    parent_index=parent_index,
                    metadata=dict(metadata or {}),
                )
            )
        return chunks


class SimpleChunker(ChunkingStrategy):
    """Fixed-size token windows with overlap. Fast, structure-blind."""

    name = "simple"

    async def split(self, text: str) -> list[TextChunk]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        return self._to_chunks(self._window_tokens(cleaned))


class RecursiveChunker(ChunkingStrategy):
    """Structure-aware: splits on paragraphs, then sentences, then tokens."""

    name = "recursive"

    async def split(self, text: str) -> list[TextChunk]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        chunk_size = self._effective_size(self._count_tokens(cleaned))
        paragraphs = re.split(r"\n{2,}", cleaned)

        segments: list[str] = []
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if self._count_tokens(paragraph) <= chunk_size:
                segments.append(paragraph)
            else:
                segments.extend(
                    s.strip() for s in _SENTENCE_RE.split(paragraph) if s.strip()
                )

        return self._to_chunks(self._pack_segments(segments, chunk_size=chunk_size))


class HierarchicalChunker(ChunkingStrategy):
    """Two-level parent/child split on document headings.

    Children carry their heading path both as retrieval metadata and as an
    embedding-text prefix, so vectors encode where the chunk lives in the
    document without polluting the stored content.
    """

    name = "hierarchical"

    async def split(self, text: str) -> list[TextChunk]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []

        sections = self._split_sections(cleaned)
        chunk_size = min(
            self._max_chunk_size,
            self._effective_size(self._count_tokens(cleaned)),
        )

        chunks: list[TextChunk] = []
        next_index = 0
        for parent_index, (heading_path, body) in enumerate(sections):
            paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
            pieces = self._pack_segments(paragraphs, chunk_size=chunk_size)
            section_chunks = self._to_chunks(
                pieces,
                parent_index=parent_index,
                embedding_prefix=heading_path,
                metadata={"heading_path": heading_path, "parent_index": parent_index}
                if heading_path
                else {"parent_index": parent_index},
                start_index=next_index,
            )
            chunks.extend(section_chunks)
            next_index += len(section_chunks)
        return chunks

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str, str]]:
        """Split markdown-ish text into (heading path, body) sections."""
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return [("", text)]

        sections: list[tuple[str, str]] = []
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))

        path: list[tuple[int, str]] = []
        for i, match in enumerate(matches):
            level = len(match.group(1))
            title = match.group(2).strip()
            path = [(lvl, t) for lvl, t in path if lvl < level]
            path.append((level, title))

            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[match.end() : end].strip()
            if body:
                heading_path = " > ".join(t for _, t in path)
                sections.append((heading_path, body))
        return sections or [("", text)]


class SemanticChunker(ChunkingStrategy):
    """Embedding-driven boundaries: break where consecutive sentence
    similarity drops below the configured percentile, then pack to size."""

    name = "semantic"

    def __init__(
        self,
        *,
        embed_fn: EmbedFn,
        breakpoint_percentile: float = 95.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._embed_fn = embed_fn
        self._breakpoint_percentile = min(99.0, max(50.0, breakpoint_percentile))

    async def split(self, text: str) -> list[TextChunk]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []

        sentences = [s.strip() for s in _SENTENCE_RE.split(cleaned) if s.strip()]
        chunk_size = self._effective_size(self._count_tokens(cleaned))
        if len(sentences) < 3:
            return self._to_chunks(self._pack_segments(sentences or [cleaned], chunk_size=chunk_size))

        vectors = await self._embed_fn(sentences)
        distances = [
            1.0 - self._cosine(vectors[i], vectors[i + 1])
            for i in range(len(vectors) - 1)
        ]
        threshold = self._percentile(distances, self._breakpoint_percentile)

        segments: list[str] = []
        current: list[str] = [sentences[0]]
        for i, distance in enumerate(distances):
            if distance >= threshold:
                segments.append(" ".join(current))
                current = []
            current.append(sentences[i + 1])
        if current:
            segments.append(" ".join(current))

        return self._to_chunks(self._pack_segments(segments, chunk_size=chunk_size))

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = (percentile / 100.0) * (len(ordered) - 1)
        low = int(math.floor(rank))
        high = int(math.ceil(rank))
        if low == high:
            return ordered[low]
        fraction = rank - low
        return ordered[low] + (ordered[high] - ordered[low]) * fraction


CHUNKING_STRATEGIES: tuple[str, ...] = (
    SimpleChunker.name,
    RecursiveChunker.name,
    HierarchicalChunker.name,
    SemanticChunker.name,
)


class ChunkerFactory:
    """Builds the requested chunking strategy; the only construction point."""

    @classmethod
    def build(
        cls,
        strategy: str,
        *,
        max_chunk_size: int,
        chunk_overlap: int,
        min_chunk_size: int | None = None,
        target_chunks: int = 8,
        embed_fn: EmbedFn | None = None,
        breakpoint_percentile: float = 95.0,
    ) -> ChunkingStrategy:
        normalized = (strategy or "").strip().lower()
        common: dict[str, Any] = {
            "max_chunk_size": max_chunk_size,
            "chunk_overlap": chunk_overlap,
            "min_chunk_size": min_chunk_size,
            "target_chunks": target_chunks,
        }

        if normalized == SimpleChunker.name:
            return SimpleChunker(**common)
        if normalized == RecursiveChunker.name:
            return RecursiveChunker(**common)
        if normalized == HierarchicalChunker.name:
            return HierarchicalChunker(**common)
        if normalized == SemanticChunker.name:
            if embed_fn is None:
                raise ValidationError(
                    "The semantic chunking strategy requires an embedding function."
                )
            return SemanticChunker(
                embed_fn=embed_fn,
                breakpoint_percentile=breakpoint_percentile,
                **common,
            )

        raise ValidationError(
            f"Unknown chunking strategy '{strategy}'. "
            f"Valid strategies: {', '.join(CHUNKING_STRATEGIES)}."
        )


build_chunker = ChunkerFactory.build
