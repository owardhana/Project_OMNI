"""Date-cursor state for the literature pipeline (Feature 2, P3).

Both the nightly forward catch-up and the historical backfill walk PubMed by
*entry date* (``EXTRACTION_DATE_TYPE=edat`` — the date a record was added to PubMed) in
fixed windows, persisting progress on a singleton ``:ExtractionCursor`` node. Entry date,
not publication date: ``pdat`` defaults year-only pub dates to Jan 1, so every Jan 1 piles
up ~123k records and would truncate the backfill; ``edat`` is a clean per-record partition.
Progress is a **date**, not a moving ``reldate`` window, so a crash or redeploy resumes
exactly where it left off — and because ``stage_verdict`` MERGEs are idempotent, redoing a
partially-finished chunk is safe.

Two cursors, anchored at ``A = today - lag`` when the pipeline is activated:
  - ``forward-catchup``   walks ``[A+1 .. now-lag]`` upward — the nightly job. Its
    frontier trails ``today`` by ``lag`` days so late-indexed papers aren't skipped.
  - ``backward-historical`` walks ``[floor .. A]`` downward to ``floor_date`` — the
    always-on backfill; pausable, resumes on startup.

The window arithmetic (`next_forward_window` / `next_backward_window`) is pure and
unit-tested; the DB helpers below are thin MERGE/SET wrappers.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

FORWARD = "forward-catchup"
BACKWARD = "backward-historical"

# status values
RUNNING = "running"   # actively processing / should be (auto-resumed on startup)
PAUSED = "paused"     # deliberately stopped; startup leaves it alone
IDLE = "idle"         # forward: nothing pending right now (next cron will re-check)
DONE = "done"         # backward: reached floor_date; nothing left to do


# --- pure window arithmetic (no DB, no I/O) --------------------------------------

def next_forward_window(
    cursor_date: date, today: date, lag_days: int, chunk_days: int
) -> tuple[date, date] | None:
    """Next [mindate, maxdate] for the forward walk, or None if caught up.

    ``cursor_date`` = newest date fully processed. The frontier is ``today - lag`` so
    PubMed's indexing lag can't strand recent papers. Windows are ``chunk_days`` wide,
    clamped to the frontier."""
    frontier = today - timedelta(days=lag_days)
    if cursor_date >= frontier:
        return None
    start = cursor_date + timedelta(days=1)
    end = min(cursor_date + timedelta(days=chunk_days), frontier)
    return (start, end)


def next_backward_window(
    cursor_date: date, floor_date: date, chunk_days: int
) -> tuple[date, date] | None:
    """Next [mindate, maxdate] for the backward walk, or None if the floor is reached.

    ``cursor_date`` = oldest date fully processed (everything in ``[cursor_date .. A]``
    is done). The next window sits just below it, clamped to ``floor_date``."""
    if cursor_date <= floor_date:
        return None
    end = cursor_date - timedelta(days=1)
    start = max(cursor_date - timedelta(days=chunk_days), floor_date)
    return (start, end)


def iso(d: date) -> str:
    return d.isoformat()


def parse(d: str) -> date:
    return date.fromisoformat(d.replace("/", "-"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- DB helpers (singleton :ExtractionCursor per name) ---------------------------

async def get_cursor(session, name: str) -> dict | None:
    rows = await (await session.run(
        "MATCH (c:ExtractionCursor {name: $name}) RETURN properties(c) AS p", name=name
    )).data()
    return rows[0]["p"] if rows else None


async def ensure_forward_cursor(session, anchor: date) -> dict:
    """Create the forward cursor at ``anchor`` if absent (ON CREATE only, so a re-run
    never rewinds progress); return its current state. Lets the nightly job self-heal
    even if the backfill was never explicitly started."""
    await session.run(
        """
        MERGE (c:ExtractionCursor {name: $name})
        ON CREATE SET c.direction = 'forward', c.cursor_date = $anchor,
                      c.status = $running, c.chunks_done = 0,
                      c.pmids_processed = 0, c.candidates_staged = 0,
                      c.created_at = $now, c.updated_at = $now
        """,
        name=FORWARD, anchor=iso(anchor), running=RUNNING, now=_now(),
    )
    return await get_cursor(session, FORWARD)


async def ensure_backward_cursor(session, anchor: date, floor_date: date) -> dict:
    """Create the backward (backfill) cursor if absent. ``cursor_date = anchor + 1`` so
    the first window includes the anchor day itself. ON CREATE only — restarting the
    backfill resumes, never rewinds."""
    await session.run(
        """
        MERGE (c:ExtractionCursor {name: $name})
        ON CREATE SET c.direction = 'backward',
                      c.cursor_date = $start, c.floor_date = $floor,
                      c.status = $running, c.chunks_done = 0,
                      c.pmids_processed = 0, c.candidates_staged = 0,
                      c.created_at = $now, c.updated_at = $now
        """,
        name=BACKWARD, start=iso(anchor + timedelta(days=1)),
        floor=iso(floor_date), running=RUNNING, now=_now(),
    )
    return await get_cursor(session, BACKWARD)


async def set_status(session, name: str, status: str) -> dict | None:
    await session.run(
        "MATCH (c:ExtractionCursor {name: $name}) SET c.status = $status, c.updated_at = $now",
        name=name, status=status, now=_now(),
    )
    return await get_cursor(session, name)


async def advance_cursor(
    session, name: str, new_cursor_date: date, window: tuple[date, date],
    n_pmids: int, n_candidates: int,
) -> None:
    """Commit one finished chunk: move the cursor and bump the running counters. Called
    only after the whole window is processed, so the persisted date is always a
    fully-covered boundary."""
    await session.run(
        """
        MATCH (c:ExtractionCursor {name: $name})
        SET c.cursor_date = $cur,
            c.last_window = $win,
            c.chunks_done = coalesce(c.chunks_done, 0) + 1,
            c.pmids_processed = coalesce(c.pmids_processed, 0) + $npmids,
            c.candidates_staged = coalesce(c.candidates_staged, 0) + $ncand,
            c.updated_at = $now
        """,
        name=name, cur=iso(new_cursor_date),
        win=f"{iso(window[0])}..{iso(window[1])}",
        npmids=n_pmids, ncand=n_candidates, now=_now(),
    )


async def running_cursors(session) -> list[dict]:
    """Cursors whose persisted status is RUNNING — used for startup auto-resume after a
    redeploy (the common interruption). PAUSED/DONE/IDLE are left untouched."""
    rows = await (await session.run(
        "MATCH (c:ExtractionCursor {status: $running}) RETURN properties(c) AS p",
        running=RUNNING,
    )).data()
    return [r["p"] for r in rows]


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
