"""Cursor-driven extraction pipeline (Feature 2, P3): nightly forward catch-up +
always-on historical backfill.

Both directions are the same loop over ``ExtractionAgent.process_window`` — the only
differences are the window function, the terminal status, and which window edge the
cursor advances to. The loop:

  1. reads the persisted cursor (date + status);
  2. stops if status != RUNNING  → honours a pause set between chunks, and lets a
     redeploy interrupt cleanly (startup re-launches RUNNING cursors — see main.py);
  3. computes the next date window, or finishes (forward → IDLE, backward → DONE);
  4. probe-then-shrinks the window under EXTRACTION_MAX_PMIDS_PER_CHUNK;
  5. processes it, then advances the cursor **only after the whole chunk completes**
     (crash mid-chunk just redoes it — stage_verdict MERGEs are idempotent).

Backlog / throttling (the user's "handle it gracefully" requirement): if a chunk sees
LLM errors (sustained rate-limiting), the cursor is NOT advanced — the loop backs off
and retries the SAME window, so no data is skipped. After EXTRACTION_HTTP_MAX_RETRIES
consecutive stalls on one window it advances anyway (a loud log), so a single
pathological window can never wedge the pipeline forever.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import httpx

from backend.agents.extraction_agent import ExtractionAgent, extraction_agent, new_stats
from backend.config import settings
from backend.db.neo4j_client import get_session
from backend.extraction import cursor as cur
from backend.extraction.ingest import count_pmids_in_range

logger = logging.getLogger(__name__)


def _next_window(direction: str, cursor_date: date, floor_date: date | None):
    if direction == "forward":
        return cur.next_forward_window(
            cursor_date, cur.today_utc(),
            settings.EXTRACTION_FORWARD_LAG_DAYS, settings.EXTRACTION_FORWARD_CHUNK_DAYS,
        )
    return cur.next_backward_window(
        cursor_date, floor_date, settings.EXTRACTION_BACKFILL_CHUNK_DAYS
    )


async def _fit_window(
    http: httpx.AsyncClient, direction: str, start: date, end: date
) -> tuple[date, date, int]:
    """Shrink the window until its esearch count fits EXTRACTION_MAX_PMIDS_PER_CHUNK,
    halving the span each probe. Forward keeps ``start`` (shrinks the newer edge);
    backward keeps ``end`` (shrinks the older edge) — so neither direction skips days.
    Bottoms out at a single day (which we then accept even if still over cap)."""
    cap = settings.EXTRACTION_MAX_PMIDS_PER_CHUNK
    while True:
        count = await count_pmids_in_range(http, cur.iso(start), cur.iso(end))
        # Stop at cap OR a single day — halving a 2-day span yields no change (span//2==1,
        # start=end-1), so a still-over-cap single day is accepted, not re-probed forever.
        if count <= cap or (end - start).days <= 1:
            return start, end, count
        span = (end - start).days
        if direction == "forward":
            end = start + timedelta(days=max(1, span // 2))
        else:
            start = end - timedelta(days=max(1, span // 2))


def _advance_target(direction: str, start: date, end: date) -> date:
    """The cursor_date a completed chunk moves to: forward covers up-to ``end``;
    backward covers down-to ``start``."""
    return end if direction == "forward" else start


async def drive_cursor(name: str, agent: ExtractionAgent | None = None) -> dict:
    """Drive one named cursor to its terminal state (caught up / floor / paused).

    Builds the gazetteer + HTTP client ONCE and reuses them across every chunk. Returns
    an accumulated summary (also written as one ExtractionRun log node)."""
    agent = agent or extraction_agent
    gazetteer = await agent.build_gazetteer()
    model = settings.EXTRACTION_MODEL
    summary = new_stats()
    summary["chunks"] = 0
    stalls = 0  # consecutive throttle-stalls on the current window

    async with httpx.AsyncClient(timeout=30.0) as http:
        while True:
            async with get_session() as session:
                state = await cur.get_cursor(session, name)
            if state is None:
                logger.warning("backfill[%s]: cursor missing — nothing to drive", name)
                break
            if state.get("status") != cur.RUNNING:
                logger.info("backfill[%s]: stopping (status=%s)", name, state.get("status"))
                break

            direction = state["direction"]
            cursor_date = cur.parse(state["cursor_date"])
            floor = cur.parse(state["floor_date"]) if state.get("floor_date") else None
            window = _next_window(direction, cursor_date, floor)
            if window is None:
                terminal = cur.IDLE if direction == "forward" else cur.DONE
                async with get_session() as session:
                    await cur.set_status(session, name, terminal)
                logger.info("backfill[%s]: reached %s at %s", name, terminal, state["cursor_date"])
                break

            start, end, n_in_window = await _fit_window(http, direction, *window)
            logger.info("backfill[%s]: window %s..%s (~%d pmids)",
                        name, cur.iso(start), cur.iso(end), n_in_window)

            chunk = new_stats()
            async with get_session() as session:
                n_pmids = await agent.process_window(
                    session, http, gazetteer, cur.iso(start), cur.iso(end), model, chunk
                )

            # Throttling backlog: don't advance past a chunk we couldn't fully evaluate.
            if chunk["llm_errors"] > 0 and stalls < settings.EXTRACTION_HTTP_MAX_RETRIES:
                stalls += 1
                backoff = settings.EXTRACTION_HTTP_BACKOFF_S * (2 ** stalls)
                logger.warning("backfill[%s]: %d LLM errors in %s..%s — retry %d after %.1fs",
                               name, chunk["llm_errors"], cur.iso(start), cur.iso(end), stalls, backoff)
                await asyncio.sleep(backoff)
                continue
            if chunk["llm_errors"] > 0:
                logger.error("backfill[%s]: window %s..%s still erroring after %d retries — "
                             "advancing with partial yield", name, cur.iso(start), cur.iso(end), stalls)
            stalls = 0

            new_date = _advance_target(direction, start, end)
            async with get_session() as session:
                await cur.advance_cursor(session, name, new_date, (start, end),
                                         n_pmids, chunk["candidate"])
            for k in chunk:
                summary[k] = summary.get(k, 0) + chunk[k]
            summary["chunks"] += 1
            logger.info("backfill[%s]: %s..%s pmids=%d candidates=%d (cursor→%s)",
                        name, cur.iso(start), cur.iso(end), n_pmids, chunk["candidate"], cur.iso(new_date))

    await agent.write_run_log_to_graph("ExtractionRun", {**summary, "cursor": name})
    logger.info("backfill[%s]: session summary %s", name, summary)
    return summary


async def start_backfill() -> dict:
    """Initialise both cursors (anchored at today−lag) and launch the backward loop. The
    forward cursor is created here too so the nightly job and the backfill share a
    consistent anchor (no seam between the two coverage regions)."""
    anchor = cur.today_utc() - timedelta(days=settings.EXTRACTION_FORWARD_LAG_DAYS)
    floor = cur.parse(settings.EXTRACTION_BACKFILL_FLOOR_DATE)
    async with get_session() as session:
        await cur.ensure_forward_cursor(session, anchor)
        back = await cur.ensure_backward_cursor(session, anchor, floor)
        # A prior run may have paused/finished it; (re)arm to RUNNING on an explicit start.
        if back and back.get("status") != cur.DONE:
            await cur.set_status(session, cur.BACKWARD, cur.RUNNING)
    return {"anchor": cur.iso(anchor), "floor": settings.EXTRACTION_BACKFILL_FLOOR_DATE}


async def arm_forward_cursor() -> None:
    """Ensure the forward cursor exists (anchored at today−lag) and is RUNNING for a
    fresh drive. Idempotent; safe to call every night."""
    anchor = cur.today_utc() - timedelta(days=settings.EXTRACTION_FORWARD_LAG_DAYS)
    async with get_session() as session:
        state = await cur.ensure_forward_cursor(session, anchor)
        if state.get("status") in (cur.IDLE, cur.DONE):
            await cur.set_status(session, cur.FORWARD, cur.RUNNING)


async def run_forward_catchup(agent: ExtractionAgent | None = None) -> dict:
    """Inline forward catch-up (arm + drive), for manual/testable use. The nightly cron
    uses ``arm_forward_cursor`` + ``launch_drive`` instead so it dedupes against a
    startup-resumed forward loop."""
    await arm_forward_cursor()
    return await drive_cursor(cur.FORWARD, agent)


# --- detached launch registry (dedupes start/resume/cron/startup within this process) ---
_active_drives: dict[str, asyncio.Task] = {}


def launch_drive(name: str) -> bool:
    """Launch a detached drive loop for a cursor unless one is already live here.
    Returns whether a new loop was started. The Oracle box runs a single backend
    process, so this in-process guard is sufficient; cross-process safety otherwise
    rests on the cursor status + the CandidateEdge uniqueness constraint."""
    existing = _active_drives.get(name)
    if existing is not None and not existing.done():
        return False

    async def _runner():
        try:
            await drive_cursor(name)
        except Exception as exc:  # noqa: BLE001
            logger.exception("backfill drive %s failed: %s", name, exc)

    task = asyncio.create_task(_runner())
    _active_drives[name] = task
    task.add_done_callback(lambda t: _active_drives.pop(name, None))
    return True


def active_drives() -> list[str]:
    return [n for n, t in _active_drives.items() if not t.done()]


async def resume_running_cursors() -> list[str]:
    """Startup hook: relaunch any cursor persisted as RUNNING. The most common
    interruption is a redeploy (git pull && up --build) mid-run, which leaves a cursor
    RUNNING with nothing driving it. PAUSED/DONE/IDLE are intentionally left alone so
    operator intent survives a restart."""
    async with get_session() as session:
        running = await cur.running_cursors(session)
    return [c["name"] for c in running if launch_drive(c["name"])]
