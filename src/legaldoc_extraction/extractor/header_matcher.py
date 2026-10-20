"""Match each clause to its mitigation by blending lexical header similarity with embedding similarity."""

from __future__ import annotations

from difflib import SequenceMatcher
from itertools import zip_longest
import re
from typing import NamedTuple

from langchain_core.embeddings import Embeddings
import numpy as np
from numpy.typing import NDArray

from ..config import settings
from ..llm_harness import DynamicEmbeddings
from .schemas import Clause, Mitigation

MIN_MATCH_SCORE = 0.5
FUZZY_WEIGHT = 0.5
EMBEDDING_WEIGHT = 0.5

type FloatMatrix = NDArray[np.float64]

_NUM_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s*")
_WORD_RE = re.compile(r"[a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")
_CLEANUP_RE = re.compile(r"[\t\r\n]+")


class Candidate(NamedTuple):
    header: str  # Normalized header
    mitigation: Mitigation


def get_embeddings() -> Embeddings:
    return DynamicEmbeddings(
        model=settings.embedding_model,
        platform=settings.platform,
    )


def normalize_header(header: str) -> str:
    """Lowercase, collapse whitespace, and trim edge colons and semicolons: `"  4.2  Equipment\\tReturn:"` → `"4.2 equipment return"`."""
    if not header:
        return ""
    norm = header.strip().lower()
    norm = _CLEANUP_RE.sub(" ", norm)
    norm = _WHITESPACE_RE.sub(" ", norm).strip()
    return norm.strip(" :;")


def _extract_numbering(header_norm: str) -> tuple[list[int], str]:
    """`"2.1.3) scope"` → `([2, 1, 3], "scope")`; unnumbered headers return `([], header_norm)`."""
    match = _NUM_PREFIX_RE.match(header_norm)
    if not match:
        return [], header_norm

    numbering_parts = [
        int(part) for part in match.group(1).split(".") if part.isdigit()
    ]
    remainder = header_norm[match.end() :].lstrip(". -:")
    return numbering_parts, remainder


def _numbering_similarity(
    lhs: list[int],
    rhs: list[int],
) -> float:
    """Shared leading levels over the deeper numbering's depth: `2.1` vs `2.1.3` scores 2/3, and `2.1` vs `3.1` scores 0."""
    if not lhs or not rhs:
        return 0.0
    if lhs == rhs:
        return 1.0

    common_prefix_count = 0
    for left_value, right_value in zip_longest(lhs, rhs):
        if left_value != right_value:
            break
        common_prefix_count += 1

    return (
        0.0
        if common_prefix_count == 0
        else common_prefix_count / max(len(lhs), len(rhs))
    )


def _compute_text_similarity(
    lhs: str,
    rhs: str,
) -> float:
    character_similarity = SequenceMatcher(None, lhs, rhs).ratio()

    lhs_tokens = set(_WORD_RE.findall(lhs))
    rhs_tokens = set(_WORD_RE.findall(rhs))
    token_similarity = 0.0
    if lhs_tokens and rhs_tokens:
        token_similarity = len(lhs_tokens & rhs_tokens) / len(lhs_tokens | rhs_tokens)

    coverage_similarity = 0.0
    if lhs and rhs:
        if lhs in rhs:
            coverage_similarity = len(lhs) / len(rhs)
        elif rhs in lhs:
            coverage_similarity = len(rhs) / len(lhs)

    return max(character_similarity, token_similarity, coverage_similarity)


def _header_similarity(
    left_header_normalized: str,
    right_header_normalized: str,
) -> float:
    """Inputs must already be normalized; when both headers are numbered, numbering affinity is half the score."""
    if not left_header_normalized or not right_header_normalized:
        return 0.0
    if left_header_normalized == right_header_normalized:
        return 1.0

    left_numbering, left_title = _extract_numbering(left_header_normalized)
    right_numbering, right_title = _extract_numbering(right_header_normalized)

    left_comparison = (
        left_title if left_title and right_title else left_header_normalized
    )
    right_comparison = (
        right_title if left_title and right_title else right_header_normalized
    )
    text_similarity = _compute_text_similarity(left_comparison, right_comparison)

    if left_numbering and right_numbering:
        numbering_similarity = _numbering_similarity(left_numbering, right_numbering)
        return (numbering_similarity + text_similarity) / 2.0

    return text_similarity


def _get_candidates(mitigations: list[Mitigation]) -> list[Candidate]:
    candidates = []
    for mit in mitigations:
        for header in mit.clause_headers:
            if norm := normalize_header(header):
                candidates.append(Candidate(norm, mit))
    return candidates


def _normalize_vectors(vectors: FloatMatrix) -> FloatMatrix:
    """L2-normalize vectors so cosine similarity reflects semantic direction, not magnitude."""
    # Guard against upstream embedding providers returning NaN/inf or malformed shapes.
    vectors = np.asarray(vectors, dtype=np.float64)
    if vectors.size == 0:
        return vectors
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)

    vectors = np.nan_to_num(vectors, nan=0.0, posinf=0.0, neginf=0.0)
    # Stable L2 norm per row (avoids overflow in sum-of-squares).
    row_scale = np.max(np.abs(vectors), axis=1, keepdims=True)
    row_scale = np.where(row_scale > 0.0, row_scale, 1.0)
    scaled = vectors / row_scale
    norms = np.sqrt(np.sum(scaled * scaled, axis=1, keepdims=True))
    safe_norms = np.where(norms > 0.0, norms, 1.0)
    normalized = scaled / safe_norms
    normalized = np.where(row_scale > 1e-10 / safe_norms, normalized, 0.0)
    return np.ascontiguousarray(normalized, dtype=np.float64)


def _compute_semantic_matrix(
    clause_headers: list[str],
    candidate_headers: list[str],
) -> FloatMatrix:
    """Calls the embeddings API once for all distinct headers; negative cosine similarities clip to 0."""
    if not clause_headers or not candidate_headers:
        return np.zeros((len(clause_headers), len(candidate_headers)), dtype=np.float64)

    embeddings = get_embeddings()

    # Embed each distinct header once; rows are re-indexed below, so this order does not affect scores.
    all_headers = list(dict.fromkeys(clause_headers + candidate_headers))
    vectors = np.asarray(embeddings.embed_documents(all_headers), dtype=np.float64)
    if (
        vectors.ndim != 2
        or vectors.shape[0] != len(all_headers)
        or vectors.shape[1] == 0
    ):
        raise ValueError(
            "Embedding response must contain one nonempty vector per unique header"
        )
    vectors = _normalize_vectors(vectors)
    indices = {header: index for index, header in enumerate(all_headers)}
    clause_vectors = vectors[[indices[header] for header in clause_headers]]
    candidate_vectors = vectors[[indices[header] for header in candidate_headers]]

    # Cosine similarity (vectors already L2-normalized). Suppress spurious BLAS warnings and sanitize output.
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        similarity = clause_vectors @ candidate_vectors.T
    similarity = np.nan_to_num(similarity, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(similarity, 0.0, 1.0)


def _compute_fuzzy_matrix(
    clause_headers: list[str],
    candidate_headers: list[str],
) -> FloatMatrix:
    return np.array(
        [
            [
                _header_similarity(clause_header, candidate_header)
                for candidate_header in candidate_headers
            ]
            for clause_header in clause_headers
        ],
        dtype=np.float64,
    )


def _select_best_matches(
    score_matrix: FloatMatrix,
    candidates: list[Candidate],
) -> list[Mitigation | None]:
    results: list[Mitigation | None] = []

    for row_scores in score_matrix:
        max_idx = np.argmax(row_scores)
        if row_scores[max_idx] >= MIN_MATCH_SCORE:
            results.append(candidates[max_idx].mitigation)
        else:
            results.append(None)

    return results


def match_batch(
    clauses: list[Clause],
    mitigations: list[Mitigation],
) -> list[Mitigation | None]:
    """Return one entry per clause: None when its header is blank or no blended score reaches MIN_MATCH_SCORE; one mitigation may match many clauses."""
    if not clauses:
        return []
    if not mitigations:
        return [None] * len(clauses)

    candidates = _get_candidates(mitigations)
    if not candidates:
        return [None] * len(clauses)

    clause_headers = [normalize_header(clause.clause_header) for clause in clauses]
    if not any(clause_headers):
        return [None] * len(clauses)
    candidate_headers = [candidate.header for candidate in candidates]

    semantic_matrix = _compute_semantic_matrix(clause_headers, candidate_headers)
    fuzzy_matrix = _compute_fuzzy_matrix(clause_headers, candidate_headers)

    score_matrix = (FUZZY_WEIGHT * fuzzy_matrix) + (EMBEDDING_WEIGHT * semantic_matrix)
    score_matrix[[not header for header in clause_headers]] = 0.0
    return _select_best_matches(score_matrix, candidates)
