"""Pure-offline tests for the date-cursor window arithmetic (Feature 2, P3).

The DB helpers and the drive loop need live Neo4j / NCBI and are smoke-tested
separately; these lock the interruption-safety-critical MATH: window boundaries,
frontier lag, floor clamping, and the probe-then-shrink halving — none of which touch
I/O. A wrong boundary here silently skips or double-covers publication dates.
"""

from datetime import date

from backend.extraction.cursor import (
    next_backward_window,
    next_forward_window,
    parse,
    iso,
)


# --- forward walk ---------------------------------------------------------------

def test_forward_window_is_day_after_cursor_up_to_chunk():
    # cursor at Jan 10, today Jan 20, lag 2 -> frontier Jan 18. chunk 3.
    win = next_forward_window(date(2026, 1, 10), date(2026, 1, 20), lag_days=2, chunk_days=3)
    assert win == (date(2026, 1, 11), date(2026, 1, 13))


def test_forward_window_clamps_to_frontier():
    # cursor Jan 16, frontier Jan 18 (today 20 - lag 2), chunk 5 -> clamp end to 18.
    win = next_forward_window(date(2026, 1, 16), date(2026, 1, 20), lag_days=2, chunk_days=5)
    assert win == (date(2026, 1, 17), date(2026, 1, 18))


def test_forward_none_when_caught_up_to_frontier():
    # cursor already at/after frontier -> nothing to do (idle until time passes).
    assert next_forward_window(date(2026, 1, 18), date(2026, 1, 20), lag_days=2, chunk_days=7) is None
    assert next_forward_window(date(2026, 1, 25), date(2026, 1, 20), lag_days=2, chunk_days=7) is None


def test_forward_lag_keeps_recent_days_out_of_reach():
    # With lag=2 and today=20, Jan 19 and 20 are never in a window (indexing buffer).
    win = next_forward_window(date(2026, 1, 17), date(2026, 1, 20), lag_days=2, chunk_days=7)
    assert win == (date(2026, 1, 18), date(2026, 1, 18))


# --- backward walk --------------------------------------------------------------

def test_backward_window_is_below_cursor_by_chunk():
    win = next_backward_window(date(2026, 1, 20), floor_date=date(2005, 1, 1), chunk_days=7)
    assert win == (date(2026, 1, 13), date(2026, 1, 19))


def test_backward_window_clamps_to_floor():
    win = next_backward_window(date(2005, 1, 5), floor_date=date(2005, 1, 1), chunk_days=7)
    assert win == (date(2005, 1, 1), date(2005, 1, 4))


def test_backward_none_at_or_below_floor():
    assert next_backward_window(date(2005, 1, 1), floor_date=date(2005, 1, 1), chunk_days=7) is None
    assert next_backward_window(date(2004, 6, 1), floor_date=date(2005, 1, 1), chunk_days=7) is None


# --- full-coverage / no-gap property --------------------------------------------

def _walk_days(next_window, cursor: date, advance, chunk_days: int, **kw) -> list[date]:
    """Replay the drive loop's date coverage: repeatedly take a window, collect its
    days, and advance the cursor to the edge the real loop persists."""
    from datetime import timedelta
    covered: list[date] = []
    while True:
        win = next_window(cursor, chunk_days=chunk_days, **kw)
        if win is None:
            break
        start, end = win
        d = start
        while d <= end:
            covered.append(d)
            d += timedelta(days=1)
        cursor = advance(start, end)
    return covered


def test_backward_walk_covers_every_day_to_floor_exactly_once():
    from datetime import timedelta
    floor, anchor = date(2025, 12, 1), date(2025, 12, 31)
    covered = _walk_days(
        lambda c, **k: next_backward_window(c, floor_date=floor, chunk_days=k["chunk_days"]),
        anchor + timedelta(days=1),      # ensure_backward_cursor starts at anchor+1
        advance=lambda start, end: start,  # advance_cursor moves to the older edge
        chunk_days=7,
    )
    expected = [floor + timedelta(days=i) for i in range((anchor - floor).days + 1)]
    assert sorted(covered) == expected        # every day present
    assert len(covered) == len(set(covered))  # none twice


def test_forward_walk_covers_up_to_frontier_exactly_once():
    from datetime import timedelta
    anchor, today, lag = date(2026, 1, 1), date(2026, 1, 15), 2
    covered = _walk_days(
        lambda c, **k: next_forward_window(c, today, lag_days=lag, chunk_days=k["chunk_days"]),
        anchor,                            # forward cursor starts at the anchor
        advance=lambda start, end: end,    # advance_cursor moves to the newer edge
        chunk_days=3,
    )
    frontier = today - timedelta(days=lag)
    expected = [anchor + timedelta(days=i + 1) for i in range((frontier - anchor).days)]
    assert sorted(covered) == expected
    assert len(covered) == len(set(covered))


def test_iso_parse_roundtrip_accepts_both_separators():
    assert iso(parse("2005/01/01")) == "2005-01-01"
    assert iso(parse("2026-07-21")) == "2026-07-21"


def test_rate_limiter_spaces_calls_and_unlimited_is_instant():
    """The free-tier LLM limiter must space concurrent acquires by 60/rate seconds so a
    burst can't 429-storm; per_minute<=0 disables it (instant)."""
    import asyncio
    import time

    from backend.extraction.ratelimit import AsyncRateLimiter

    async def run():
        rl = AsyncRateLimiter(per_minute=120)  # 0.5s spacing
        t0 = time.monotonic()
        offsets: list[float] = []

        async def one():
            await rl.acquire()
            offsets.append(time.monotonic() - t0)

        await asyncio.gather(*[one() for _ in range(4)])
        offsets.sort()
        gaps = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
        assert all(g >= 0.45 for g in gaps), gaps  # ~0.5s apart

        rl0 = AsyncRateLimiter(0)
        t = time.monotonic()
        await rl0.acquire()
        assert time.monotonic() - t < 0.05  # unlimited: no wait

    asyncio.run(run())


def test_fit_window_terminates_at_single_day_when_always_over_cap(monkeypatch):
    """Regression: a window that stays over-cap must bottom out at a single day, not
    re-probe NCBI forever (halving a 2-day span yields no change)."""
    import asyncio

    from backend.config import settings
    from backend.extraction import backfill

    async def _always_over(http, mn, mx, term=None):
        return settings.EXTRACTION_MAX_PMIDS_PER_CHUNK + 1

    monkeypatch.setattr(backfill, "count_pmids_in_range", _always_over)

    for direction in ("forward", "backward"):
        start, end, count = asyncio.run(
            backfill._fit_window(None, direction, date(2020, 1, 1), date(2020, 1, 8))
        )
        assert (end - start).days <= 1  # terminated at a single day, did not hang
