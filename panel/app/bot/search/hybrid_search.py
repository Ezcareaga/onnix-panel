"""Reciprocal Rank Fusion — merges SQL and vector search results.

Combines ranked lists from traditional SQL queries and semantic vector
searches using the RRF algorithm. Items appearing in both lists receive
higher combined scores.

Reference: Cormack, Clarke & Butt (2009) — Reciprocal Rank Fusion
outperforms Condorcet and individual rank methods.
"""
from __future__ import annotations


def reciprocal_rank_fusion(
    sql_results: list[int],
    vector_results: list[int],
    k: int = 60,
) -> list[int]:
    """Merge two ranked lists of property IDs using Reciprocal Rank Fusion.

    Each item receives a score of 1/(k + rank) for each list it appears in.
    Items in both lists accumulate scores from both, ranking them higher.

    Args:
        sql_results: Property IDs ordered by SQL relevance.
        vector_results: Property IDs ordered by vector similarity.
        k: Smoothing constant (default 60). Lower k amplifies top ranks.

    Returns:
        Merged list of property IDs sorted by combined RRF score (desc).
    """
    scores: dict[int, float] = {}
    for rank, pid in enumerate(sql_results, start=1):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    for rank, pid in enumerate(vector_results, start=1):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda pid: scores[pid], reverse=True)
