"""Tests for Reciprocal Rank Fusion — hybrid search result merging.

Tests cover:
- Identical lists (3+3 same) → same order preserved
- Disjoint lists (no overlap) → all 6 items, both present
- Overlapping lists (partial) → shared items rank highest
- Empty SQL list → vector results returned
- Both empty → empty result
- k parameter effect on ranking
"""

from app.bot.search.hybrid_search import reciprocal_rank_fusion


# ===========================================================================
# TestReciprocalRankFusion — 6 pure Python tests
# ===========================================================================


class TestReciprocalRankFusion:
    """Verify RRF merging logic with various list combinations."""

    def test_rrf_identical_lists(self):
        """Identical lists [1,2,3] + [1,2,3] → [1,2,3] same order."""
        result = reciprocal_rank_fusion([1, 2, 3], [1, 2, 3])
        assert result == [1, 2, 3]

    def test_rrf_disjoint_lists(self):
        """Disjoint [1,2,3] + [4,5,6] → all 6 present."""
        result = reciprocal_rank_fusion([1, 2, 3], [4, 5, 6])
        assert len(result) == 6
        assert set(result) == {1, 2, 3, 4, 5, 6}
        # Items at rank 1 in each list should be in top positions
        # 1 and 4 both at rank 1, so they get the same score
        # 1/(60+1) each. Top 2 should be {1, 4}
        assert set(result[:2]) == {1, 4}

    def test_rrf_overlapping_lists(self):
        """Overlapping [1,2,3] + [3,2,4] → 2 and 3 rank highest (in both)."""
        result = reciprocal_rank_fusion([1, 2, 3], [3, 2, 4])
        # 2 appears at rank 2 in both → score = 2 * 1/(60+2) = 2/62
        # 3 appears at rank 3 in sql, rank 1 in vector → 1/63 + 1/61
        # 1 appears only in sql at rank 1 → 1/61
        # 4 appears only in vector at rank 3 → 1/63
        # 2: 2/62 ≈ 0.03226
        # 3: 1/63 + 1/61 ≈ 0.01587 + 0.01639 = 0.03227
        # So 3 and 2 are very close, both should be in top 2
        assert set(result[:2]) == {2, 3}

    def test_rrf_empty_sql_list(self):
        """Empty SQL + [1,2,3] → [1,2,3] from vector only."""
        result = reciprocal_rank_fusion([], [1, 2, 3])
        assert result == [1, 2, 3]

    def test_rrf_empty_both_lists(self):
        """Both empty → empty result."""
        result = reciprocal_rank_fusion([], [])
        assert result == []

    def test_rrf_k_parameter(self):
        """With k=1, top-ranked items get much higher scores."""
        # k=1: rank 1 score = 1/(1+1) = 0.5, rank 2 = 1/(1+2) = 0.333
        # k=60: rank 1 score = 1/61, rank 2 = 1/62 (almost equal)
        # With k=1, [1,2] + [2,1]:
        #   1: 1/2 + 1/3 = 5/6 ≈ 0.833
        #   2: 1/3 + 1/2 = 5/6 ≈ 0.833
        # Both have same score (symmetric), so order doesn't matter.
        # Better test: [1,2,3] + [3] with k=1
        #   1: 1/2 = 0.5
        #   2: 1/3 ≈ 0.333
        #   3: 1/4 + 1/2 = 0.75  → 3 is top!
        result = reciprocal_rank_fusion([1, 2, 3], [3], k=1)
        assert result[0] == 3, "With k=1, item 3 (in both lists) should rank first"
