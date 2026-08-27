def calculate_total_pages(total: int, per_page: int) -> int:
    """Calculate total number of pages for pagination.

    Returns at least 1 even when total is 0, so the UI always shows page 1.
    """
    return max(1, (total + per_page - 1) // per_page)
