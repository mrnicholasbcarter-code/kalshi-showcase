"""v40 intraday/weekly kill-switch.

superbot must call is_killed() before routing each trade.
evaluate_kill_switch() assesses current drawdown + stella state.
write_kill_marker() is called automatically when triggered.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

KILL_THRESHOLDS: dict[str, float] = {
    "intraday_dd_pct": 0.03,         # 3% of session-start bankroll
    "weekly_dd_pct": 0.08,           # 8%
    "consecutive_black_states": 1,   # any BLACK halts new trades
}

_DATA_DIR = Path(__file__).parent.parent / "data" / "v40"
_KILL_LOCK = _DATA_DIR / "kill_switch.lock"
_SESSION_HALT_MARKER = _DATA_DIR / "session_halt.lock"
_LONGSHOT_HALT_MARKER = _DATA_DIR / "longshot_halt.lock"
_DISCOVERY_HALT_MARKER = _DATA_DIR / "discovery_halt.lock"


@dataclass
class PortfolioState:
    """Snapshot of live/paper portfolio state passed in by the caller."""
    session_start_bankroll: float
    current_bankroll: float
    weekly_start_bankroll: float
    stella_state: str  # 'GREEN' | 'YELLOW' | 'RED' | 'BLACK'


def _query(db_path: str, sql: str, params: tuple = ()) -> list[tuple]:
    with sqlite3.connect(db_path) as c:
        return c.execute(sql, params).fetchall()


def evaluate_kill_switch(
    paper_db_path: str = "data/v40/v40_paper.db",
    state: PortfolioState | None = None,
) -> dict[str, Any]:
    db = Path(paper_db_path)
    if not db.is_absolute():
        log.warning("evaluate_kill_switch: relative db path %r resolved relative to module", paper_db_path)
        db = Path(__file__).parent.parent / paper_db_path

    # --- derive metrics ---
    intraday_dd: float = 0.0
    weekly_dd: float = 0.0
    stella_state: str = "GREEN"

    if state is not None:
        if state.session_start_bankroll > 0:
            intraday_dd = max(0.0, (state.session_start_bankroll - state.current_bankroll) / state.session_start_bankroll)
        if state.weekly_start_bankroll > 0:
            weekly_dd = max(0.0, (state.weekly_start_bankroll - state.current_bankroll) / state.weekly_start_bankroll)
        stella_state = state.stella_state
    else:
        # derive from DB
        try:
            day_start = time.time() - 86400
            # Safety #13: use ISO week start (Monday 00:00 UTC) not rolling 7d
            _now_utc = datetime.now(timezone.utc)
            _week_start_dt = (_now_utc - timedelta(days=_now_utc.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            week_start = _week_start_dt.timestamp()
            pnl_day = _query(db_path=str(db), sql="SELECT COALESCE(SUM(pnl),0) FROM v40_trades WHERE opened_at >= ?", params=(day_start,))
            pnl_week = _query(db_path=str(db), sql="SELECT COALESCE(SUM(pnl),0) FROM v40_trades WHERE opened_at >= ?", params=(week_start,))
            bankroll_row = _query(db_path=str(db), sql="SELECT bankroll FROM v40_session_state ORDER BY ts DESC LIMIT 1")
            bankroll = bankroll_row[0][0] if bankroll_row else 100.0
            intraday_pnl = pnl_day[0][0] if pnl_day else 0.0
            weekly_pnl = pnl_week[0][0] if pnl_week else 0.0
            session_start = bankroll - intraday_pnl
            weekly_start = bankroll - weekly_pnl
            intraday_dd = max(0.0, -intraday_pnl / session_start) if session_start > 0 else 0.0
            weekly_dd = max(0.0, -weekly_pnl / weekly_start) if weekly_start > 0 else 0.0
        except Exception as e:
            log.warning("kill_switch db read failed: %s", e)

        try:
            stella_row = _query(db_path=str(db), sql="SELECT state FROM v40_stella_log ORDER BY ts DESC LIMIT 1")
            stella_state = stella_row[0][0] if stella_row else "GREEN"
        except Exception as e:
            log.warning("kill_switch stella_log read failed: %s", e)

    t = KILL_THRESHOLDS
    triggered = False
    reason: Literal["intraday_dd", "weekly_dd", "stella_black"] | None = None
    action: Literal["halt_new_trades", "halt_and_close", "ok"] = "ok"

    if stella_state == "BLACK":
        triggered = True
        reason = "stella_black"
        action = "halt_and_close"
    elif weekly_dd >= t["weekly_dd_pct"]:
        triggered = True
        reason = "weekly_dd"
        action = "halt_new_trades"
    elif intraday_dd >= t["intraday_dd_pct"]:
        triggered = True
        reason = "intraday_dd"
        action = "halt_new_trades"

    result: dict[str, Any] = {
        "triggered": triggered,
        "reason": reason,
        "current": {
            "intraday_dd": round(intraday_dd, 4),
            "weekly_dd": round(weekly_dd, 4),
            "stella_state": stella_state,
        },
        "action": action,
    }

    if triggered and reason:
        write_kill_marker(reason)
    elif _KILL_LOCK.exists():
        # AUTO-RECONCILE (bug-022): the lock persists on disk independent of the
        # live governor. After a FALSE trip (e.g. stale/inflated peak anchor) the
        # live drawdown recovers but the lock keeps the loop halted forever until a
        # human deletes the file — that caused a silent overnight halt. If THIS
        # evaluation is not triggered AND live drawdown is comfortably below the
        # thresholds (hysteresis margin to avoid flapping), clear the stale lock
        # with a loud audit event. Stella BLACK is NOT auto-cleared (hard stop).
        _auto_reconcile_kill_marker(intraday_dd=intraday_dd, weekly_dd=weekly_dd, stella_state=stella_state)

    return result


# Hysteresis: only auto-clear when live drawdown is well under the trip threshold.
# 2026-06-07 (swarm review D1): tightened 0.5 -> 0.25 AND added a sticky-halt requirement.
# At 0.5 the recovery band (50%-25% of threshold) was wide enough for the bot to oscillate
# around the trip line — halt, recover just past 0.5x, resume, trade back over the line,
# halt again — extending exposure exactly where the policy should be hardest. Now:
#   (1) margin 0.25 -> must recover to well under a quarter of the trip level, and
#   (2) the recovered state must PERSIST for _RECONCILE_MIN_CONSECUTIVE evaluations before
#       the lock auto-clears, so a single lucky loop at the line cannot un-halt the bot.
_RECONCILE_MARGIN = 0.25
_RECONCILE_MIN_CONSECUTIVE = 3
_RECONCILE_OK_STREAK = _DATA_DIR / "kill_reconcile_streak.json"


def _auto_reconcile_kill_marker(*, intraday_dd: float, weekly_dd: float, stella_state: str) -> None:
    """Clear a stale kill_switch.lock when the live governor has provably recovered.

    Safe to call autonomously (unlike clear_kill_marker which is the human path):
    only fires when this loop's evaluation did NOT trigger AND both drawdowns sit
    comfortably below their thresholds for _RECONCILE_MIN_CONSECUTIVE consecutive evals.
    Never clears on stella BLACK.
    """
    if not _KILL_LOCK.exists():
        _RECONCILE_OK_STREAK.unlink(missing_ok=True)  # no lock → reset streak
        return
    if stella_state == "BLACK":
        _RECONCILE_OK_STREAK.unlink(missing_ok=True)
        return  # hard stop — never auto-clear
    t = KILL_THRESHOLDS
    intraday_ok = intraday_dd < t["intraday_dd_pct"] * _RECONCILE_MARGIN
    weekly_ok = weekly_dd < t["weekly_dd_pct"] * _RECONCILE_MARGIN
    if not (intraday_ok and weekly_ok):
        # still near the line — reset the recovery streak and leave the lock
        _RECONCILE_OK_STREAK.unlink(missing_ok=True)
        return
    # Recovered THIS eval — require the recovery to persist before clearing (sticky halt).
    try:
        _streak = int(json.loads(_RECONCILE_OK_STREAK.read_text()).get("n", 0)) if _RECONCILE_OK_STREAK.exists() else 0
    except Exception:  # noqa: BLE001
        _streak = 0
    _streak += 1
    if _streak < _RECONCILE_MIN_CONSECUTIVE:
        try:
            _RECONCILE_OK_STREAK.write_text(json.dumps({"n": _streak, "ts": time.time()}))
        except Exception:  # noqa: BLE001
            pass
        log.info(
            "kill_switch recovery streak %d/%d (intraday=%.4f weekly=%.4f) — not clearing yet",
            _streak, _RECONCILE_MIN_CONSECUTIVE, intraday_dd, weekly_dd,
        )
        return
    _RECONCILE_OK_STREAK.unlink(missing_ok=True)  # cleared — reset streak
    try:
        prior = json.loads(_KILL_LOCK.read_text()) if _KILL_LOCK.exists() else {}
    except Exception:  # noqa: BLE001
        prior = {}
    if not _KILL_LOCK.exists():
        return
    log.warning(
        "kill_switch AUTO-RECONCILED: live drawdown recovered "
        "(intraday=%.4f weekly=%.4f, thresholds=%.3f/%.3f). Prior halt reason=%s killed_at=%s",
        intraday_dd, weekly_dd, t["intraday_dd_pct"], t["weekly_dd_pct"],
        prior.get("reason"), prior.get("killed_at"),
    )
    _KILL_LOCK.unlink(missing_ok=True)
    # Audit trail in safety.db risk_events so the auto-clear is never silent.
    try:
        from kalshi.safety import Alert, default_bus  # noqa: PLC0415
        default_bus().emit(Alert(
            severity="warning", kind="halt_cleared",
            message="v40 kill_switch AUTO-CLEARED (live drawdown recovered)",
            detail={
                "prior_reason": prior.get("reason"),
                "prior_killed_at": prior.get("killed_at"),
                "intraday_dd": round(intraday_dd, 4),
                "weekly_dd": round(weekly_dd, 4),
            },
        ))
    except Exception as _ae:  # noqa: BLE001
        log.warning("auto-reconcile alert emit failed (non-fatal): %s", _ae)


def write_kill_marker(
    reason: str,
    *,
    drawdown_pct: float | None = None,
    bankroll_at_halt: float | None = None,
) -> None:
    """Write data/v40/kill_switch.lock — superbot checks this before each route.

    Persists across watchdog restarts. Manual recovery: delete the file after
    human review (see clear_kill_marker).
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    already_killed = _KILL_LOCK.exists()  # alert only on the not-killed -> killed transition
    marker = {
        "killed": True,
        "reason": reason,
        "killed_at": datetime.now(tz=timezone.utc).isoformat(),
        "drawdown_pct": drawdown_pct,
        "bankroll_at_halt": bankroll_at_halt,
        "clear_instructions": "Delete this file to re-enable trading after manual review.",
    }
    _KILL_LOCK.write_text(json.dumps(marker, indent=2))
    log.warning("kill_switch ENGAGED reason=%s drawdown_pct=%s bankroll=%s", reason, drawdown_pct, bankroll_at_halt)
    # S1: a halt must be LOUD (the migration's #1 lesson — the bot halted silently
    # for 8 days). Alert once per halt episode; never let alerting block the kill.
    if not already_killed:
        try:
            try:
                from dotenv import load_dotenv  # noqa: PLC0415
                # explicit repo .env path (safety-critical: don't rely on cwd/find_dotenv)
                load_dotenv(_DATA_DIR.parent.parent / ".env")  # override=False: systemd env wins
            except Exception:  # noqa: BLE001
                pass
            from kalshi.safety import Alert, default_bus  # noqa: PLC0415
            default_bus().emit(Alert(
                severity="critical", kind="halt",
                message=f"v40 trading HALTED: {reason}",
                detail={"reason": reason, "drawdown_pct": drawdown_pct, "bankroll_at_halt": bankroll_at_halt},
            ))
        except Exception as _ae:  # noqa: BLE001
            log.warning("halt alert emit failed (non-fatal): %s", _ae)


def clear_kill_marker(reason: str) -> None:
    """Remove kill_switch.lock — REQUIRES human action, do not call autonomously.

    Log the reason for audit trail before deleting.
    """
    if _KILL_LOCK.exists():
        log.warning("kill_switch CLEARED by human: reason=%s", reason)
        _KILL_LOCK.unlink()


def _write_session_halt_marker(reason: str) -> None:
    """Write persistent session-halt marker (used by should_block_trades)."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _SESSION_HALT_MARKER.write_text(json.dumps({
        "halted": True,
        "reason": reason,
        "halted_at": datetime.now(tz=timezone.utc).isoformat(),
    }, indent=2))


def _is_session_halted() -> tuple[bool, str]:
    """Return (halted, reason) from persistent session halt marker.
    Auto-clears if marker is from a previous UTC day (Safety #14).
    """
    if not _SESSION_HALT_MARKER.exists():
        return False, ""
    try:
        data = json.loads(_SESSION_HALT_MARKER.read_text())
        if not data.get("halted"):
            return False, ""

        halted_at_str = data.get("halted_at", "")
        if halted_at_str:
            halted_at = datetime.fromisoformat(halted_at_str)
            now_utc = datetime.now(timezone.utc)
            if halted_at.date() < now_utc.date():
                log.warning("session_halt marker EXPIRED (new UTC day), unlinking")
                _SESSION_HALT_MARKER.unlink(missing_ok=True)
                return False, ""

        return True, data.get("reason", "session_halt")
    except Exception as e:
        log.warning("session_halt marker read failed: %s", e)
        return True, "corrupt_marker"  # fail-closed
    return False, ""


def write_longshot_halt_marker(reason: str, daily_loss_usd: float) -> None:
    """Write longshot-specific halt marker. Does NOT affect main-bucket trading."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LONGSHOT_HALT_MARKER.write_text(json.dumps({
        "halted": True,
        "reason": reason,
        "daily_loss_usd": daily_loss_usd,
        "halted_at": datetime.now(tz=timezone.utc).isoformat(),
        "resets": "UTC midnight",
        "clear_instructions": "Clears automatically at UTC midnight, or delete this file for immediate reset.",
    }, indent=2))
    log.warning("LONGSHOT_HALT: %s daily_loss_usd=%.2f", reason, daily_loss_usd)


def write_discovery_halt_marker(reason: str, daily_loss_usd: float) -> None:
    """Write discovery-specific halt marker. Does NOT affect main-bucket trading."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _DISCOVERY_HALT_MARKER.write_text(json.dumps({
        "halted": True,
        "reason": reason,
        "daily_loss_usd": daily_loss_usd,
        "halted_at": datetime.now(tz=timezone.utc).isoformat(),
        "resets": "UTC midnight",
        "clear_instructions": "Clears automatically at UTC midnight, or delete this file for immediate reset.",
    }, indent=2))
    log.warning("DISCOVERY_HALT: %s daily_loss_usd=%.2f", reason, daily_loss_usd)


def _is_discovery_halted() -> tuple[bool, str]:
    """Return (halted, reason). Auto-clears if marker is from a previous UTC day."""
    if not _DISCOVERY_HALT_MARKER.exists():
        return False, ""
    try:
        data = json.loads(_DISCOVERY_HALT_MARKER.read_text())
        if not data.get("halted"):
            return False, ""
        halted_at_str = data.get("halted_at", "")
        if halted_at_str:
            halted_at = datetime.fromisoformat(halted_at_str)
            now_utc = datetime.now(tz=timezone.utc)
            if halted_at.date() < now_utc.date():
                _DISCOVERY_HALT_MARKER.unlink(missing_ok=True)
                log.info("discovery_halt marker expired (new UTC day), cleared")
                return False, ""
        return True, data.get("reason", "discovery_daily_loss_cap")
    except Exception as e:
        log.warning("discovery_halt marker read failed: %s", e)
        return True, "corrupt_discovery_marker"  # fail-closed
    return False, ""


def _is_longshot_halted() -> tuple[bool, str]:
    """Return (halted, reason). Auto-clears if marker is from a previous UTC day."""
    if not _LONGSHOT_HALT_MARKER.exists():
        return False, ""
    try:
        data = json.loads(_LONGSHOT_HALT_MARKER.read_text())
        if not data.get("halted"):
            return False, ""
        halted_at_str = data.get("halted_at", "")
        if halted_at_str:
            halted_at = datetime.fromisoformat(halted_at_str)
            now_utc = datetime.now(tz=timezone.utc)
            # Auto-expire: different UTC date = new day
            if halted_at.date() < now_utc.date():
                _LONGSHOT_HALT_MARKER.unlink(missing_ok=True)
                log.info("longshot_halt marker expired (new UTC day), cleared")
                return False, ""
        return True, data.get("reason", "longshot_daily_loss_cap")
    except Exception as e:
        log.warning("longshot_halt marker read failed: %s", e)
        return True, "corrupt_longshot_marker"  # fail-closed
    return False, ""


def is_killed() -> bool:
    """Return True if kill_switch.lock exists and is marked killed.
    Auto-clears if the lock is from a previous UTC day.
    """
    if not _KILL_LOCK.exists():
        return False
    try:
        # Safety #14: Auto-clear stale kill_switch.lock from previous UTC day.
        # This prevents the "chicken-and-egg" halt where no trades settle → no reset.
        stat = _KILL_LOCK.stat()
        mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        now_utc = datetime.now(tz=timezone.utc)
        if mtime_dt.date() < now_utc.date():
            log.warning("kill_switch lock EXPIRED (mtime=%s), unlinking", mtime_dt.isoformat())
            _KILL_LOCK.unlink(missing_ok=True)
            return False

        data = json.loads(_KILL_LOCK.read_text())
        return bool(data.get("killed", True))
    except Exception as e:
        log.warning("kill_switch lock read/stat failed: %s", e)
        return True  # fail-closed on corrupt lock


def should_block_trades(bucket: str = "main") -> tuple[bool, str]:
    """Return (blocked, reason) combining kill-switch lock + bucket-specific halt.

    Args:
        bucket: "main" (default), "longshot", or "discovery".
            - "main": checks kill_switch.lock + session_halt.lock (existing behavior).
            - "longshot": checks kill_switch.lock + longshot_halt.lock only;
              a main-bucket session halt does NOT block longshot trades, and
              a longshot halt does NOT block main-bucket trades.
            - "discovery": checks kill_switch.lock + discovery_halt.lock only;
              session_halt.lock is NOT checked — discovery is exempt from main
              session halt so edges can be validated even when main is halted.

    Superbot imports this as the single gate before routing any trade.
    Backward compatible: callers that don't pass bucket= get "main" behavior.
    """
    # kill_switch.lock (intraday/weekly/stella) blocks ALL buckets
    if is_killed():
        try:
            data = json.loads(_KILL_LOCK.read_text())
            reason = data.get("reason", "kill_switch")
        except Exception:
            reason = "kill_switch"
        return True, reason

    if bucket == "longshot":
        halted, reason = _is_longshot_halted()
        if halted:
            return True, reason
        return False, ""

    if bucket == "discovery":
        halted, reason = _is_discovery_halted()
        if halted:
            return True, reason
        return False, ""

    # bucket == "main" (default)
    halted, reason = _is_session_halted()
    if halted:
        return True, reason

    return False, ""
