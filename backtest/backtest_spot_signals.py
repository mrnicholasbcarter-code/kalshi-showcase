#!/usr/bin/env python3
"""
backtest_spot_signals.py — Validate vol_regime + momentum_5m against live trades
=================================================================================
Fetches historical Binance hourly candles (~21 days), reconstructs vol_regime
at each trade's timestamp, then shows P&L / WR / volume by regime bucket.

Also simulates: what if we skipped HIGH/EXTREME vol trades?

Usage:
    python3 backtest_spot_signals.py
    python3 backtest_spot_signals.py --skip-vol high extreme
    python3 backtest_spot_signals.py --since 2026-04-10
    python3 backtest_spot_signals.py --series KXBNB15M
"""

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

DB_PATH = Path(__file__).parent / "kalshi_trader_v35.db"
BINANCE_KLINES = "https://api.binance.us/api/v3/klines"

SERIES_TO_BINANCE = {
    "KXBTC15M":  "BTCUSDT",
    "KXETH15M":  "ETHUSDT",
    "KXSOL15M":  "SOLUSDT",
    "KXXRP15M":  "XRPUSDT",
    "KXDOGE15M": "DOGEUSDT",
    "KXBNB15M":  "BNBUSDT",
    "KXHYPE15M": None,  # no spot pair
}

VOL_THRESHOLDS = {"low": 0.45, "normal": 0.80, "high": 1.20}  # % of price; extreme=>1.20
MOMENTUM_FLAT_BAND = 0.05  # % — below this → "flat"


# ── Binance data fetch ────────────────────────────────────────────────────────

def fetch_hourly(symbol: str, limit: int = 500) -> Optional[list]:
    try:
        r = requests.get(
            BINANCE_KLINES,
            params={"symbol": symbol, "interval": "1h", "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] Hourly fetch failed {symbol}: {e}")
        return None


def fetch_1m_at(symbol: str, ts_utc: datetime, window: int = 7) -> Optional[list]:
    """Fetch `window` 1-min candles ending at ts_utc."""
    end_ms = int(ts_utc.timestamp() * 1000)
    start_ms = end_ms - window * 60 * 1000
    try:
        r = requests.get(
            BINANCE_KLINES,
            params={
                "symbol": symbol,
                "interval": "1m",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": window,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── ATR + regime ──────────────────────────────────────────────────────────────

def _atr14(candles: list) -> Optional[float]:
    if len(candles) < 15:
        return None
    trs = []
    for i in range(1, 15):
        high = float(candles[-i][2])
        low = float(candles[-i][3])
        prev_close = float(candles[-(i + 1)][4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs)


def _vol_regime(atr: float, close: float) -> tuple[str, float]:
    atr_pct = (atr / close) * 100
    if atr_pct < VOL_THRESHOLDS["low"]:
        regime = "low"
    elif atr_pct < VOL_THRESHOLDS["normal"]:
        regime = "normal"
    elif atr_pct < VOL_THRESHOLDS["high"]:
        regime = "high"
    else:
        regime = "extreme"
    return regime, round(atr_pct, 3)


def regime_at(candles: list, trade_ts: datetime) -> tuple[Optional[str], Optional[float]]:
    """
    Find the candle bucket containing trade_ts and compute vol_regime
    from the 14 hourly candles ending at that bucket.
    candles: newest last, each candle[0] = open_ms.
    """
    trade_ms = int(trade_ts.timestamp() * 1000)
    # find last candle whose open_ms <= trade_ms
    idx = None
    for i, c in enumerate(candles):
        if int(c[0]) <= trade_ms:
            idx = i
    if idx is None or idx < 14:
        return None, None
    window = candles[: idx + 1]
    atr = _atr14(window)
    if atr is None:
        return None, None
    close = float(candles[idx][4])
    if close == 0:
        return None, None
    return _vol_regime(atr, close)


def momentum_at(candles_1m: Optional[list]) -> tuple[float, str]:
    """Compute 5-min momentum from 1-min candles (first→last close change)."""
    if not candles_1m or len(candles_1m) < 2:
        return 0.0, "unknown"
    old_close = float(candles_1m[0][4])
    new_close = float(candles_1m[-1][4])
    if old_close == 0:
        return 0.0, "unknown"
    pct = round((new_close - old_close) / old_close * 100, 4)
    if pct > MOMENTUM_FLAT_BAND:
        direction = "up"
    elif pct < -MOMENTUM_FLAT_BAND:
        direction = "down"
    else:
        direction = "flat"
    return pct, direction


# ── Main ──────────────────────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> datetime:
    ts_str = ts_str.replace("+00:00", "").replace("Z", "")
    try:
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None, help="ISO date e.g. 2026-04-10")
    p.add_argument("--series", default=None, help="e.g. KXBNB15M")
    p.add_argument(
        "--skip-vol",
        nargs="+",
        default=[],
        metavar="REGIME",
        help="Simulate skipping trades in these regimes: low normal high extreme",
    )
    p.add_argument(
        "--skip-momentum",
        nargs="+",
        default=[],
        metavar="DIR",
        help="Simulate skipping trades with these momentum directions: up down flat",
    )
    p.add_argument("--no-1m", action="store_true", help="Skip 1-min momentum (faster)")
    cfg = p.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    where = ["mode='live'", "outcome!='pending'", "outcome IS NOT NULL"]
    params: list = []
    if cfg.since:
        where.append("opened_at >= ?")
        params.append(cfg.since)
    if cfg.series:
        where.append("ticker LIKE ?")
        params.append(cfg.series + "%")

    trades = conn.execute(
        f"SELECT ticker, side, opened_at, outcome, pnl, cost, contracts FROM trades WHERE {' AND '.join(where)} ORDER BY opened_at",
        params,
    ).fetchall()
    conn.close()

    if not trades:
        print("No trades found.")
        return

    print(f"\nLoading {len(trades)} trades, fetching Binance candles per series...")

    # Group trades by series, pre-fetch candles once per series
    series_trades: dict[str, list] = defaultdict(list)
    for t in trades:
        series = t["ticker"].split("-")[0]
        series_trades[series].append(t)

    # Per-regime stats: {regime: {wins, losses, pnl, cost, skipped_pnl}}
    regime_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0, "cost": 0.0})
    momentum_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0, "cost": 0.0})

    actual_pnl = sum(t["pnl"] or 0.0 for t in trades)
    sim_skip_vol_pnl = actual_pnl
    sim_skip_mom_pnl = actual_pnl
    skip_vol_count = skip_mom_count = 0

    for series, strades in series_trades.items():
        sym = SERIES_TO_BINANCE.get(series)
        if sym is None:
            print(f"  {series}: no Binance pair, skipping")
            for t in strades:
                regime_stats["no_data"]["wins" if t["outcome"] == "win" else "losses"] += 1
                regime_stats["no_data"]["pnl"] += t["pnl"] or 0.0
                regime_stats["no_data"]["cost"] += t["cost"] or 0.0
            continue

        print(f"  Fetching hourly candles for {series} ({sym})...")
        hourly = fetch_hourly(sym, limit=500)
        if not hourly:
            for t in strades:
                regime_stats["fetch_error"]["wins" if t["outcome"] == "win" else "losses"] += 1
            continue

        for t in strades:
            trade_ts = _parse_ts(t["opened_at"])
            regime, _ = regime_at(hourly, trade_ts)
            if regime is None:
                regime = "unknown"

            outcome_key = "wins" if t["outcome"] == "win" else "losses"
            pnl = t["pnl"] or 0.0
            cost = t["cost"] or 0.0

            regime_stats[regime][outcome_key] += 1
            regime_stats[regime]["pnl"] += pnl
            regime_stats[regime]["cost"] += cost

            # Vol simulation
            if regime in cfg.skip_vol:
                sim_skip_vol_pnl -= pnl
                skip_vol_count += 1

            # Momentum (optional — slow due to per-trade API call)
            mom_dir = "unknown"
            if not cfg.no_1m:
                candles_1m = fetch_1m_at(sym, trade_ts, window=7)
                _, mom_dir = momentum_at(candles_1m)

            momentum_stats[mom_dir][outcome_key] += 1
            momentum_stats[mom_dir]["pnl"] += pnl
            momentum_stats[mom_dir]["cost"] += cost

            if mom_dir in cfg.skip_momentum:
                sim_skip_mom_pnl -= pnl
                skip_mom_count += 1

    # ── Report ────────────────────────────────────────────────────────────────

    print(f"\n{'='*62}")
    print(f"  SPOT SIGNAL BACKTEST  —  {len(trades):,} trades")
    print(f"{'='*62}")
    print(f"  since={cfg.since or 'all'}  series={cfg.series or 'all'}")
    print(f"  Actual P&L: ${actual_pnl:+.2f}")
    print()

    def _wr(s):
        total = s["wins"] + s["losses"]
        return s["wins"] / total if total else 0.0

    def _roc(s):
        return (s["pnl"] / s["cost"] * 100) if s["cost"] > 0 else 0.0

    print(f"  {'Regime':<12} {'Trades':>7} {'WR%':>6} {'P&L':>9} {'RoC%':>7} {'Cost':>9}")
    print(f"  {'-'*12} {'-'*7} {'-'*6} {'-'*9} {'-'*7} {'-'*9}")
    vol_order = ["low", "normal", "high", "extreme", "unknown", "no_data", "fetch_error"]
    for regime in vol_order:
        s = regime_stats.get(regime)
        if not s:
            continue
        total = s["wins"] + s["losses"]
        if total == 0:
            continue
        flag = " ← SKIP" if regime in cfg.skip_vol else ""
        print(
            f"  {regime:<12} {total:>7,} {_wr(s)*100:>5.1f}% {s['pnl']:>+9.2f} {_roc(s):>+6.1f}% {s['cost']:>9.2f}{flag}"
        )

    if cfg.skip_vol:
        delta = sim_skip_vol_pnl - actual_pnl
        print(f"\n  Skip-vol sim ({', '.join(cfg.skip_vol)}): trades skipped={skip_vol_count}")
        print(f"    Actual P&L:    ${actual_pnl:+.2f}")
        print(f"    Sim P&L:       ${sim_skip_vol_pnl:+.2f}")
        print(f"    Delta:         ${delta:+.2f}  ({delta/max(abs(actual_pnl),0.01)*100:+.1f}%)")

    if not cfg.no_1m:
        print(f"\n  {'Momentum':<12} {'Trades':>7} {'WR%':>6} {'P&L':>9} {'RoC%':>7}")
        print(f"  {'-'*12} {'-'*7} {'-'*6} {'-'*9} {'-'*7}")
        for mom in ["up", "down", "flat", "unknown"]:
            s = momentum_stats.get(mom)
            if not s:
                continue
            total = s["wins"] + s["losses"]
            if total == 0:
                continue
            flag = " ← SKIP" if mom in cfg.skip_momentum else ""
            print(
                f"  {mom:<12} {total:>7,} {_wr(s)*100:>5.1f}% {s['pnl']:>+9.2f} {_roc(s):>+6.1f}%{flag}"
            )

        if cfg.skip_momentum:
            delta = sim_skip_mom_pnl - actual_pnl
            print(f"\n  Skip-momentum sim ({', '.join(cfg.skip_momentum)}): trades skipped={skip_mom_count}")
            print(f"    Actual P&L:    ${actual_pnl:+.2f}")
            print(f"    Sim P&L:       ${sim_skip_mom_pnl:+.2f}")
            print(f"    Delta:         ${delta:+.2f}  ({delta/max(abs(actual_pnl),0.01)*100:+.1f}%)")

    print(f"\n{'='*62}\n")


if __name__ == "__main__":
    main()
