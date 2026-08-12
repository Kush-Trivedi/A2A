"""Chunking strategies with ADAPTIVE sizing — no configured chunk sizes.

Every strategy derives its numbers from the document itself (see ChunkPlan):

    chunk_size = clamp(8 * sqrt(doc_tokens), 256, 1024)
    overlap    = chunk_size * clamp(0.15 - 0.02 * log10(doc_tokens), 0.05, 0.15)

A 1k-token memo gets ~250-token chunks with ~13% overlap; a 100k-token
manual gets 1024-token chunks with 5% overlap. The 1024 cap is where
embedding quality and retrieval granularity degrade — a model limit, not a
config choice. Documents at or under 256 tokens become exactly one chunk.
Structure-aware strategies use 60% of the overlap (headings already carry
context across boundaries) and never overlap across sections.

Strategies:
    plain        — token windows. Structure-blind, fastest.
    recursive    — paragraphs, then sentences, then tokens. The default.
    hierarchical — heading sections; children carry their heading path as
                   retrieval metadata AND as an embedding-text prefix.
    hybrid       — hierarchical sectioning + embedding-driven semantic
                   boundaries inside oversized sections. Best quality;
                   costs embedding calls at ingest.
"""

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

_SINGLE_CHUNK_MAX_TOKENS = 256
_MIN_CHUNK_TOKENS = 256
_MAX_CHUNK_TOKENS = 1024
_SIZE_FACTOR = 8.0            # chunk_size = _SIZE_FACTOR * sqrt(doc_tokens)
_OVERLAP_BASE = 0.15          # overlap ratio at ~1 token, shrinking with size
_OVERLAP_SLOPE = 0.02         # ratio drops this much per 10x tokens
_OVERLAP_FLOOR = 0.05
_STRUCTURED_OVERLAP_FACTOR = 0.6
_SEMANTIC_BREAKPOINT_PERCENTILE = 90.0


@dataclass(frozen=True)
class ChunkPlan:
    """The adaptive numbers for one document."""

    total_tokens: int
    chunk_size: int
    overlap: int
    single_chunk: bool


def plan_for(total_tokens: int, *, structured: bool = False) -> ChunkPlan:
    if total_tokens <= _SINGLE_CHUNK_MAX_TOKENS:
        return ChunkPlan(
            total_tokens=total_tokens,
            chunk_size=max(1, total_tokens),
            overlap=0,
            single_chunk=True,
        )
    size = round(_SIZE_FACTOR * math.sqrt(total_tokens))
    size = max(_MIN_CHUNK_TOKENS, min(size, _MAX_CHUNK_TOKENS))
    ratio = _OVERLAP_BASE - _OVERLAP_SLOPE * math.log10(total_tokens)
    ratio = max(_OVERLAP_FLOOR, min(ratio, _OVERLAP_BASE))
    if structured:
        ratio *= _STRUCTURED_OVERLAP_FACTOR
    return ChunkPlan(
        total_tokens=total_tokens,
        chunk_size=size,
        overlap=round(size * ratio),
        single_chunk=False,
    )


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
    structured: bool = False

    def __init__(self, *, encoding_name: str = "cl100k_base") -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)

    async def split(self, text: str) -> list[TextChunk]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        plan = plan_for(self._count_tokens(cleaned), structured=self.structured)
        if plan.single_chunk:
            return self._to_chunks([cleaned])
        return await self._split(cleaned, plan)

    @abstractmethod
    async def _split(self, text: str, plan: ChunkPlan) -> list[TextChunk]:
        """Split cleaned, multi-chunk text according to the plan."""

    def _count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def _window_tokens(self, text: str, plan: ChunkPlan) -> list[str]:
        """Fixed token windows with the plan's overlap."""
        tokens = self._encoding.encode(text)
        total = len(tokens)
        if total == 0:
            return []
        size = plan.chunk_size
        overlap = min(plan.overlap, size - 1) if size > 1 else 0
        step = max(1, size - overlap)

        windows: list[str] = []
        start = 0
        while start < total:
            content = self._encoding.decode(tokens[start : start + size]).strip()
            if content:
                windows.append(content)
            if start + size >= total:
                break
            start += step
        return windows

    def _pack_segments(self, segments: list[str], plan: ChunkPlan) -> list[str]:
        """Greedily pack text segments into chunks up to the plan size."""
        packed: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            seg_tokens = self._count_tokens(segment)
            if seg_tokens > plan.chunk_size:
                if current:
                    packed.append("\n\n".join(current))
                    current, current_tokens = [], 0
                packed.extend(self._window_tokens(segment, plan))
                continue
            if current_tokens + seg_tokens > plan.chunk_size and current:
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

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str, str]]:
        """Markdown-ish text into (heading path, body) sections."""
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
                sections.append((" > ".join(t for _, t in path), body))
        return sections or [("", text)]


class PlainChunker(ChunkingStrategy):
    """Adaptive token windows with overlap. Structure-blind, fastest."""

    name = "plain"

    async def _split(self, text: str, plan: ChunkPlan) -> list[TextChunk]:
        return self._to_chunks(self._window_tokens(text, plan))


class RecursiveChunker(ChunkingStrategy):
    """Structure-aware: paragraphs, then sentences, then token windows."""

    name = "recursive"

    async def _split(self, text: str, plan: ChunkPlan) -> list[TextChunk]:
        segments: list[str] = []
        for paragraph in re.split(r"\n{2,}", text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if self._count_tokens(paragraph) <= plan.chunk_size:
                segments.append(paragraph)
            else:
                segments.extend(
                    s.strip() for s in _SENTENCE_RE.split(paragraph) if s.strip()
                )
        return self._to_chunks(self._pack_segments(segments, plan))


class HierarchicalChunker(ChunkingStrategy):
    """Heading sections as parents; children carry their heading path both
    as retrieval metadata and as an embedding-text prefix, so vectors know
    where the chunk lives without polluting the stored content."""

    name = "hierarchical"
    structured = True

    async def _split(self, text: str, plan: ChunkPlan) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        next_index = 0
        for parent_index, (heading_path, body) in enumerate(self._split_sections(text)):
            paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
            pieces = self._pack_segments(paragraphs, plan)
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


class HybridChunker(ChunkingStrategy):
    """Hierarchical sectioning + semantic boundaries: sections that fit the
    plan pass through whole; oversized sections break where consecutive
    sentence-embedding similarity drops (the topic shifts), then pack to
    size. Highest retrieval quality; costs embedding calls at ingest."""

    name = "hybrid"
    structured = True

    def __init__(self, *, embed_fn: EmbedFn, encoding_name: str = "cl100k_base") -> None:
        super().__init__(encoding_name=encoding_name)
        self._embed_fn = embed_fn

    async def _split(self, text: str, plan: ChunkPlan) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        next_index = 0
        for parent_index, (heading_path, body) in enumerate(self._split_sections(text)):
            if self._count_tokens(body) <= plan.chunk_size:
                pieces = [body.strip()] if body.strip() else []
            else:
                segments = await self._semantic_segments(body)
                pieces = self._pack_segments(segments, plan)
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

    async def _semantic_segments(self, body: str) -> list[str]:
        sentences = [s.strip() for s in _SENTENCE_RE.split(body) if s.strip()]
        if len(sentences) < 3:
            return sentences or [body]

        vectors = await self._embed_fn(sentences)
        distances = [
            1.0 - self._cosine(vectors[i], vectors[i + 1])
            for i in range(len(vectors) - 1)
        ]
        threshold = self._percentile(distances, _SEMANTIC_BREAKPOINT_PERCENTILE)

        segments: list[str] = []
        current: list[str] = [sentences[0]]
        for i, distance in enumerate(distances):
            if distance >= threshold:
                segments.append(" ".join(current))
                current = []
            current.append(sentences[i + 1])
        if current:
            segments.append(" ".join(current))
        return segments

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
        low = math.floor(rank)
        high = math.ceil(rank)
        if low == high:
            return ordered[low]
        return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


CHUNKING_STRATEGIES: tuple[str, ...] = (
    PlainChunker.name,
    RecursiveChunker.name,
    HierarchicalChunker.name,
    HybridChunker.name,
)
DEFAULT_CHUNKING_STRATEGY = RecursiveChunker.name


def build_chunker(strategy: str, *, embed_fn: EmbedFn | None = None) -> ChunkingStrategy:
    normalized = (strategy or "").strip().lower()
    if normalized == PlainChunker.name:
        return PlainChunker()
    if normalized == RecursiveChunker.name:
        return RecursiveChunker()
    if normalized == HierarchicalChunker.name:
        return HierarchicalChunker()
    if normalized == HybridChunker.name:
        if embed_fn is None:
            raise ValidationError(
                "The hybrid chunking strategy needs the embedding endpoint "
                "(microsoft.azure.azure_foundry.embedding in the env yaml)."
            )
        return HybridChunker(embed_fn=embed_fn)
    raise ValidationError(
        f"Unknown chunking strategy '{strategy}'. "
        f"Valid strategies: {', '.join(CHUNKING_STRATEGIES)}."
    )
