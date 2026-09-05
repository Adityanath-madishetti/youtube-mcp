"""Quota accounting: the constraint the whole server is designed around."""

import pytest

from youtube_mcp.youtube import ENDPOINT_COSTS, QuotaExceeded, QuotaLedger


def test_standard_list_calls_cost_one_unit():
    ledger = QuotaLedger(100)
    for endpoint in ("subscriptions", "playlistItems", "videos", "channels", "playlists"):
        ledger.charge(endpoint)
    assert ledger.spent == 5


def test_search_costs_one_hundred_units():
    """The 100x cliff that justifies never calling search.list."""
    assert ENDPOINT_COSTS["search"] == 100
    ledger = QuotaLedger(200)
    ledger.charge("search")
    assert ledger.spent == 100


def test_budget_is_enforced_before_spending():
    ledger = QuotaLedger(10)
    for _ in range(10):
        ledger.charge("videos")
    with pytest.raises(QuotaExceeded):
        ledger.charge("videos")
    # The rejected call must not have been counted.
    assert ledger.spent == 10


def test_expensive_call_rejected_when_it_would_overshoot():
    ledger = QuotaLedger(50)
    ledger.charge("videos")
    with pytest.raises(QuotaExceeded, match="quota budget"):
        ledger.charge("search")
    assert ledger.spent == 1
