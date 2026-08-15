"""v40 deterministic risk rails. Model-agnostic. Veto or shrink ML-approved trades."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data" / "v40"
_EDGE_KILLS_DIR = _DATA_DIR / "edge_kills"


# Default underlying-coin clusters for crypto-15M correlation cap.
COIN_CLUSTERS = {
    "KXBTC15M": "BTC", "KXBTCD": "BTC",
    "KXETH15M": "ETH", "KXETHD": "ETH",
    "KXSOL15M": "SOL", "KXSOLD": "SOL",
    "KXXRP15M": "XRP", "KXXRPD": "XRP",
    "KXDOGE15M": "DOGE", "KXDOGED": "DOGE",
    "KXBNB15M": "BNB", "KXBNBD": "BNB",
    "KXHYPE15M": "HYPE", "KXHYPED": "HYPE",
    "KXADA15M": "ADA",
    "KXBCH15M": "BCH",
}


def _default_bankroll() -> float:
    """Safety #7: read bankroll from env V40_BANKROLL_START, fall back to 100.0."""
    raw = os.environ.get("V40_BANKROLL_START", "").strip()
    if raw:
        try:
            val = float(raw)
            log.info("RiskConfig: bankroll_session_start=%.2f (from V40_BANKROLL_START)", val)
            return val
        except ValueError:
            log.warning("RiskConfig: invalid V40_BANKROLL_START=%r, using 100.0", raw)
    log.info("RiskConfig: bankroll_session_start=100.0 (default)")
    return 100.0


@dataclass
class RiskConfig:
    max_trade_usd: float = 5.0
    max_portfolio_usd: float = 50.0
    max_cluster_usd: float = 20.0
    session_drawdown_stop_pct: float = field(default_factory=lambda: float(os.environ.get("V40_SESSION_DD_STOP_PCT", "0.08")))
    min_liquidity_contracts: int = field(default_factory=lambda: int(os.environ.get("V40_MIN_LIQUIDITY_CONTRACTS", "5")))
    max_spread: float = field(default_factory=lambda: float(os.environ.get("V40_MAX_SPREAD", "0.05")))
    bankroll_session_start: float = field(default_factory=_default_bankroll)
    min_ev_after_fees: float = 0.0
    max_conformal_width: float = field(default_factory=lambda: float(os.environ.get("V40_MAX_CONFORMAL_WIDTH", "0.35")))
    min_p_low: float = field(default_factory=lambda: float(os.environ.get("V40_MIN_P_LOW", "0.50")))
    stale_open_cap: int = 20
    bot_daily_loss_cap_pct: float = field(default_factory=lambda: float(os.environ.get("V40_BOT_DAILY_LOSS_CAP_PCT", "0.10")))  # Safety #14: per-bot daily loss cap
    # 2026-05-26 T1#2: correlated-exposure caps. Prevent cluster wipeout pattern.
    # Defaults tuned 2026-05-26 via 30d counterfactual replay: per-expiry=4 retains
    # most legitimate multi-leg winning days, per-series-longshot=1 catches the
    # 5/26 longshot cluster wipeout (one rule firing 8x on same expiry).
    max_open_per_expiry: int = field(default_factory=lambda: int(os.environ.get("V40_MAX_OPEN_PER_EXPIRY", "4")))
    max_open_per_series_longshot: int = field(default_factory=lambda: int(os.environ.get("V40_MAX_OPEN_PER_SERIES_LONGSHOT", "1")))
    max_open_per_series_main: int = field(default_factory=lambda: int(os.environ.get("V40_MAX_OPEN_PER_SERIES_MAIN", "3")))


@dataclass
class CandidateTrade:
    ticker: str
    side: str
    intended_usd: float
    p_win: float | None = None
    p_lo: float | None = None
    p_hi: float | None = None
    expected_value: float | None = None
    fee_per_dollar: float = 0.0
    rule_name: str = ""                  # 2026-05-26 T1#1: rule context for YES-side kill guard
    expiry_key: str = ""                 # 2026-05-26 T1#2: expiry id (e.g. "26MAY260700-00") for correlation cap
    series: str = ""                     # 2026-05-26 T1#2: series id (e.g. "KXBTC15M") for correlation cap


@dataclass
class RiskDecision:
    approved: bool
    sized_usd: float
    reason: str
    checks: list[dict[str, object]] = field(default_factory=list)


def _longshot_max_daily_loss() -> float:
    """Read V40_LONGSHOT_MAX_DAILY_LOSS_USD env var (default 25.0)."""
    raw = os.environ.get("V40_LONGSHOT_MAX_DAILY_LOSS_USD", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            log.warning("RiskConfig: invalid V40_LONGSHOT_MAX_DAILY_LOSS_USD=%r, using 25.0", raw)
    return 25.0


def _discovery_max_daily_loss() -> float:
    """Read V40_DISCOVERY_MAX_DAILY_LOSS_USD env var (default 75.0)."""
    raw = os.environ.get("V40_DISCOVERY_MAX_DAILY_LOSS_USD", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            log.warning("RiskConfig: invalid V40_DISCOVERY_MAX_DAILY_LOSS_USD=%r, using 75.0", raw)
    return 75.0


def _discovery_max_trade_usd() -> float:
    """Read V40_DISCOVERY_MAX_TRADE_USD env var (default 2.0)."""
    raw = os.environ.get("V40_DISCOVERY_MAX_TRADE_USD", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            log.warning("RiskConfig: invalid V40_DISCOVERY_MAX_TRADE_USD=%r, using 2.0", raw)
    return 2.0


@dataclass
class PortfolioState:
    open_usd: float = 0.0
    cluster_usd: dict[str, float] = field(default_factory=dict)
    open_count: int = 0
    session_pnl: float = 0.0
    halt: bool = False
    halt_reason: str | None = None
    consecutive_losses: int = 0
    # C7: per-edge consecutive loss tracking keyed by config_stem
    per_edge_losses: dict[str, int] = field(default_factory=dict)
    # Safety #14: daily pnl tracking keyed by bot_id
    daily_pnl_by_bot: dict[str, float] = field(default_factory=dict)
    last_pnl_reset_day: str | None = None   # UTC date of last daily_pnl_by_bot reset
    # Long-shot bucket: separate PnL tracking so losses don't trip main halt
    main_session_pnl: float = 0.0       # realized PnL from non-longshot/non-discovery trades only
    longshot_session_pnl: float = 0.0   # realized PnL from longshot trades only
    longshot_daily_loss: float = 0.0    # sum of longshot losses today (<=0, absolute)
    longshot_halt: bool = False         # True when longshot_daily_loss exceeds cap
    # Discovery bucket: isolated capital for edge validation
    discovery_session_pnl: float = 0.0  # realized PnL from discovery trades only
    discovery_daily_loss: float = 0.0   # sum of discovery losses today (<=0)
    discovery_halt: bool = False         # True when discovery_daily_loss exceeds cap
    # 2026-05-26 T1#2: correlated-exposure tracking. Prevents the 8-13-bet-same-expiry
    # cluster wipeout pattern from 5/26 (530-30, 630-30, 645-45 batches lost 31/31 NO bets).
    open_by_expiry: dict[str, int] = field(default_factory=dict)   # expiry_key → open count
    open_by_series: dict[str, int] = field(default_factory=dict)   # series → open count


def growth_intended_usd(
    *,
    bankroll: float,
    fraction: float,
    default_intended: float,
    max_trade_usd: float,
    expected_value: float,
    p_lo: float,
    p_win: float | None = None,
    price: float | None = None,
    edge_name: str | None = None,
    market_dict: dict | None = None,
) -> float:
    """Sizing helper. Default = quality-multiplied fractional sizing.

    2026-05-26 T2#5: when V40_KELLY_SIZING=1, switch to fractional Kelly using
    calibrated p_win + market price. Off by default to avoid disruption during
    3-day validation; flip on once calibration accumulates n>=20 per bucket.

    2026-05-26 T3#11: when V40_BANDIT_ALLOC=1 and edge_name provided, also
    weight the result by a Thompson sample from the bandit posterior — edges
    with better posterior get scaled up; weak edges get scaled down. Capped
    between 0.5x and 2.0x to bound effect during ramp-up.
    """
    if (
        os.environ.get("V40_KELLY_SIZING", "0") == "1"
        and p_win is not None
        and price is not None
    ):
        try:
            from v40.features.kelly_sizing import kelly_sized_usd  # noqa: PLC0415
            # 2026-05-26 EXPANSION-G: fractional 0.25 → 0.50. Calibration is
            # doing the safety work (correcting model's 85pp overconfidence at
            # inference). 0.25× was double-protection; 0.50 lets winners compound
            # twice as fast while still half-Kelly conservative.
            _kf = float(os.environ.get("V40_KELLY_FRACTION", "0.50"))
            kelly = kelly_sized_usd(
                p_win=p_win, price=price, bankroll=bankroll,
                fractional=_kf, min_stake=0.5, max_stake=max_trade_usd,
            )
            if kelly > 0:
                base = kelly
                if os.environ.get("V40_BANDIT_ALLOC", "0") == "1" and edge_name:
                    try:
                        if (
                            os.environ.get("V40_LINUCB", "0") == "1"
                            and market_dict is not None
                        ):
                            from v40.policies.bandit_allocator import contextual_sample  # noqa: PLC0415
                            ts = contextual_sample(edge_name, market_dict)
                        else:
                            from v40.policies.bandit_allocator import thompson_sample  # noqa: PLC0415
                            ts = thompson_sample(edge_name)
                        if ts is not None:
                            # Posterior mean ~0.5 is "no info" — keep stake. Higher → scale up.
                            mult = max(0.5, min(2.0, 2.0 * ts))
                            base = base * mult
                            base = min(base, max_trade_usd)
                    except Exception as _be:
                        log.debug("bandit allocate failed: %s", _be)
                return base
        except Exception as _ke:
            log.debug("kelly sizing failed, falling back: %s", _ke)
    quality = 1.0
    if expected_value > 0.25:
        quality += 0.5
    if expected_value > 0.75:
        quality += 0.5
    if p_lo >= 0.65:
        quality += 0.25
    intended = bankroll * fraction * quality
    return max(0.5, min(max_trade_usd, max(default_intended, intended)))



# RuleHypothesis for VPIN/Kyle's Lambda:
# The Lambda measures order flow toxicity (VPIN). High VPIN indicates informed trading, 
# which for prediction markets usually means adverse selection.
# Rule: Reject trades if VPIN > VPIN_THRESHOLD.

VPIN_THRESHOLD = 0.40

def check_vpin_gate(market: dict) -> bool:
    vpin = market.get("vpin", 0.0)
    return vpin < VPIN_THRESHOLD

def evaluate_candidate(
    candidate: CandidateTrade,
    market: dict,
    cfg: RiskConfig,
    state: PortfolioState,
    *,
    risk_bucket: str = "main",
) -> RiskDecision:
    checks: list[dict[str, object]] = []
    # Main halt blocks everything
    if state.halt:
        checks.append({"name": "drawdown", "passed": False, "reason": state.halt_reason or "halted"})
        return RiskDecision(False, 0.0, f"halted: {state.halt_reason}", checks)
    # Longshot halt blocks only longshot candidates
    if risk_bucket == "longshot" and state.longshot_halt:
        checks.append({"name": "longshot_drawdown", "passed": False, "reason": "longshot_daily_loss_cap"})
        return RiskDecision(False, 0.0, "longshot_daily_loss_cap", checks)
    # Discovery halt blocks only discovery candidates
    if risk_bucket == "discovery" and state.discovery_halt:
        checks.append({"name": "discovery_drawdown", "passed": False, "reason": "discovery_daily_loss_cap"})
        return RiskDecision(False, 0.0, "discovery_daily_loss_cap", checks)
    checks.append({"name": "drawdown", "passed": True, "session_pnl": state.session_pnl, "risk_bucket": risk_bucket})
    if state.open_count >= cfg.stale_open_cap:
        checks.append({"name": "concentration", "passed": False, "open_count": state.open_count, "limit": cfg.stale_open_cap})
        return RiskDecision(False, 0.0, "stale_open_cap", checks)
    # 2026-05-26 T1#1: kill YES side from superbot:approved rule. Paper history:
    # 214 superbot:approved YES trades = -$234 (-$1.09/trade avg). NO side +$6.63/trade.
    # 2026-05-26 EXPANSION-D: domain-aware allowlist. The blanket YES kill was
    # collateral damage to explore_crypto_alt_yes_* which has 62 paper trades,
    # 64.5% WR, +$22 net. Now check the candidate's domain (passed in market
    # dict) to skip allowlisted exploration rules from YES kill.
    if (
        candidate.rule_name.startswith("superbot:approved")
        and candidate.side.upper() == "YES"
        and os.environ.get("V40_ALLOW_SUPERBOT_YES", "0") != "1"
    ):
        _domain = (market or {}).get("domain", "") if isinstance(market, dict) else ""
        # Allowlisted YES-profitable exploration rules (positive paper history)
        _yes_allowlist = (
            "explore_crypto_alt_yes_",       # 62 trades, 64.5% WR, +$22 net
        )
        if not (_domain and any(_domain.startswith(pfx) for pfx in _yes_allowlist)):
            checks.append({"name": "yes_kill", "passed": False, "rule": candidate.rule_name, "side": candidate.side, "domain": _domain})
            return RiskDecision(False, 0.0, "yes_kill:superbot_approved", checks)
    # 2026-05-26 T1#2: correlated-exposure caps. Prevent same-expiry cluster bets that
    # all lose together (530-30/630-30/645-45 wiped 31/31 NO bets on 5/26).
    if candidate.expiry_key:
        expiry_open = state.open_by_expiry.get(candidate.expiry_key, 0)
        if expiry_open >= cfg.max_open_per_expiry:
            checks.append({"name": "correlation_cap", "passed": False, "scope": "expiry", "key": candidate.expiry_key, "open": expiry_open, "limit": cfg.max_open_per_expiry})
            return RiskDecision(False, 0.0, f"max_open_per_expiry[{candidate.expiry_key}]", checks)
    if not check_vpin_gate(market):
        checks.append({"name": "vpin_gate", "passed": False, "vpin": market.get("vpin", 0.0), "threshold": VPIN_THRESHOLD})
        return RiskDecision(False, 0.0, "vpin_gate_exceeded", checks)
    if candidate.series:
        series_open = state.open_by_series.get(candidate.series, 0)
        series_cap = cfg.max_open_per_series_longshot if risk_bucket == "longshot" else cfg.max_open_per_series_main
        if series_open >= series_cap:
            checks.append({"name": "correlation_cap", "passed": False, "scope": "series", "key": candidate.series, "open": series_open, "limit": series_cap, "bucket": risk_bucket})
            return RiskDecision(False, 0.0, f"max_open_per_series[{candidate.series}]:{risk_bucket}", checks)
    ev_after_fees = (candidate.expected_value or 0.0) - candidate.fee_per_dollar
    if risk_bucket not in ("longshot", "discovery"):
        # Longshot/discovery trades skip min-EV / p_low / conformal floors
        if ev_after_fees < cfg.min_ev_after_fees:
            checks.append({"name": "kelly", "passed": False, "ev_after_fees": ev_after_fees, "min": cfg.min_ev_after_fees})
            return RiskDecision(False, 0.0, "min_ev_after_fees", checks)
        if candidate.p_lo is not None and candidate.p_lo < cfg.min_p_low:
            checks.append({"name": "kelly", "passed": False, "p_lo": candidate.p_lo, "min": cfg.min_p_low})
            return RiskDecision(False, 0.0, "p_low_too_low", checks)
        if candidate.p_lo is not None and candidate.p_hi is not None:
            width = candidate.p_hi - candidate.p_lo
            if width > cfg.max_conformal_width:
                checks.append({"name": "kelly", "passed": False, "conformal_width": width, "max": cfg.max_conformal_width})
                return RiskDecision(False, 0.0, "conformal_width", checks)
    checks.append({"name": "kelly", "passed": True, "ev_after_fees": ev_after_fees, "risk_bucket": risk_bucket})
    # Discovery bucket: cap intended_usd to discovery max trade size (not main max_trade_usd)
    _intended = candidate.intended_usd
    if risk_bucket == "discovery":
        _disc_max = _discovery_max_trade_usd()
        _intended = min(_intended, _disc_max)
        checks.append({"name": "discovery_size_cap", "passed": True, "intended_usd": _intended, "cap": _disc_max})
    approved, sized, reason, rail_checks = evaluate_rails(
        ticker=candidate.ticker, side=candidate.side, intended_usd=_intended,
        market=market, cfg=cfg, state=state,
    )
    checks.extend(rail_checks)
    return RiskDecision(approved, sized, reason, checks)


def evaluate_rails(
    *, ticker: str, side: str, intended_usd: float, market: dict,
    cfg: RiskConfig, state: PortfolioState,
) -> tuple[bool, float, str, list[dict[str, object]]]:
    checks: list[dict[str, object]] = []
    if state.halt:
        return False, 0.0, f"halted: {state.halt_reason}", [{"name": "drawdown", "passed": False, "reason": state.halt_reason or "halted"}]

    sized = min(intended_usd, cfg.max_trade_usd)
    if sized <= 0:
        return False, 0.0, "intended_usd<=0", [{"name": "kelly", "passed": False, "sized_usd": sized}]

    if state.open_usd + sized > cfg.max_portfolio_usd:
        sized = max(0.0, cfg.max_portfolio_usd - state.open_usd)
        if sized < 0.5:
            checks.append({"name": "concentration", "passed": False, "open_usd": state.open_usd, "limit": cfg.max_portfolio_usd})
            return False, 0.0, "portfolio_cap", checks
    checks.append({"name": "concentration", "passed": True, "open_usd": state.open_usd, "sized_usd": sized, "limit": cfg.max_portfolio_usd})

    series = ticker.split("-")[0] if "-" in ticker else ticker
    cluster = COIN_CLUSTERS.get(series, "OTHER")
    cluster_open = state.cluster_usd.get(cluster, 0.0)
    if cluster_open + sized > cfg.max_cluster_usd:
        sized = max(0.0, cfg.max_cluster_usd - cluster_open)
        if sized < 0.5:
            checks.append({"name": "correlation", "passed": False, "cluster": cluster, "cluster_open": cluster_open, "limit": cfg.max_cluster_usd})
            return False, 0.0, f"cluster_cap[{cluster}]", checks
    checks.append({"name": "correlation", "passed": True, "cluster": cluster, "cluster_open": cluster_open, "sized_usd": sized, "limit": cfg.max_cluster_usd})

    if market is not None:
        bid = market.get("yes_bid") if side.lower() == "yes" else market.get("no_bid")
        ask = market.get("yes_ask") if side.lower() == "yes" else market.get("no_ask")
        if bid is not None and ask is not None and (ask - bid) > cfg.max_spread:
            checks.append({"name": "liquidity", "passed": False, "spread": ask - bid, "max": cfg.max_spread})
            return False, 0.0, "spread_too_wide", checks
        size_at_top = market.get("yes_ask_sz") if side.lower() == "yes" else market.get("no_ask_sz")
        if size_at_top is not None and size_at_top < cfg.min_liquidity_contracts:
            checks.append({"name": "liquidity", "passed": False, "size_at_top": size_at_top, "min": cfg.min_liquidity_contracts})
            return False, 0.0, "thin_top_of_book", checks
        checks.append({"name": "liquidity", "passed": True, "spread": None if bid is None or ask is None else ask - bid, "size_at_top": size_at_top})

    return True, sized, "approved", checks


def _expiry_key_from_ticker(ticker: str) -> str:
    """Extract expiry segment from a Kalshi ticker like KXBTC15M-26MAY260530-30 → '26MAY260530-30'.

    Used by T1#2 correlation cap to bucket "same-expiry" exposure.
    """
    parts = ticker.split("-", 1)
    return parts[1] if len(parts) == 2 else ticker


def update_state_on_open(state: PortfolioState, ticker: str, sized_usd: float) -> None:
    state.open_usd += sized_usd
    state.open_count += 1
    series = ticker.split("-")[0] if "-" in ticker else ticker
    cluster = COIN_CLUSTERS.get(series, "OTHER")
    state.cluster_usd[cluster] = state.cluster_usd.get(cluster, 0.0) + sized_usd
    # 2026-05-26 T1#2: maintain correlation-cap counters
    expiry = _expiry_key_from_ticker(ticker)
    state.open_by_expiry[expiry] = state.open_by_expiry.get(expiry, 0) + 1
    state.open_by_series[series] = state.open_by_series.get(series, 0) + 1


def update_state_on_settle(
    state: PortfolioState,
    ticker: str,
    sized_usd: float,
    pnl: float,
    cfg: RiskConfig,
    *,
    config_stem: str | None = None,
    bot_id: str | None = None,
    risk_bucket: str = "main",
) -> None:
    """Update portfolio state after a trade settles.

    Args:
        risk_bucket: "main" (default) or "longshot".
            Longshot losses accumulate in longshot_session_pnl / longshot_daily_loss
            and do NOT count toward the session_drawdown halt threshold.
            Longshot WINS count toward longshot_session_pnl only (not main_session_pnl).
            session_pnl (total) always reflects all trades for reporting purposes.
    """
    # Lazy import to avoid circular; risk_kill_switch has no dep on risk.
    from v40.risk_kill_switch import (  # noqa: PLC0415
        write_kill_marker,
        _write_session_halt_marker,
        write_longshot_halt_marker,
        write_discovery_halt_marker,
    )

    state.open_usd = max(0.0, state.open_usd - sized_usd)
    state.open_count = max(0, state.open_count - 1)
    series = ticker.split("-")[0] if "-" in ticker else ticker
    cluster = COIN_CLUSTERS.get(series, "OTHER")
    state.cluster_usd[cluster] = max(0.0, state.cluster_usd.get(cluster, 0.0) - sized_usd)
    # 2026-05-26 T1#2: decrement correlation-cap counters; clear zero buckets
    expiry = _expiry_key_from_ticker(ticker)
    state.open_by_expiry[expiry] = max(0, state.open_by_expiry.get(expiry, 0) - 1)
    if state.open_by_expiry.get(expiry, 0) == 0:
        state.open_by_expiry.pop(expiry, None)
    state.open_by_series[series] = max(0, state.open_by_series.get(series, 0) - 1)
    if state.open_by_series.get(series, 0) == 0:
        state.open_by_series.pop(series, None)

    # --- bucket dispatch ---
    state.session_pnl += pnl  # total PnL always updated
    if risk_bucket == "longshot":
        state.longshot_session_pnl += pnl
        if pnl < 0:
            state.longshot_daily_loss += pnl  # pnl is negative; loss accumulates
    elif risk_bucket == "discovery":
        state.discovery_session_pnl += pnl
        if pnl < 0:
            state.discovery_daily_loss += pnl  # discovery losses isolated from main
        # discovery wins/losses do NOT touch main_session_pnl or daily_pnl_by_bot
    else:
        state.main_session_pnl += pnl

    # Emit structured metrics log
    log.info(
        "risk_metrics bucket=%s pnl=%.4f main_session_pnl=%.4f longshot_session_pnl=%.4f "
        "longshot_daily_loss=%.4f longshot_halt=%s discovery_session_pnl=%.4f "
        "discovery_daily_loss=%.4f discovery_halt=%s",
        risk_bucket, pnl,
        state.main_session_pnl, state.longshot_session_pnl,
        state.longshot_daily_loss, state.longshot_halt,
        state.discovery_session_pnl, state.discovery_daily_loss, state.discovery_halt,
    )

    # C7: per-edge consecutive loss tracking
    _per_edge_loss_cap = int(os.environ.get("V40_PER_EDGE_LOSS_CAP", "5"))
    if config_stem:
        if pnl < 0:
            state.per_edge_losses[config_stem] = state.per_edge_losses.get(config_stem, 0) + 1
        else:
            state.per_edge_losses[config_stem] = 0
        if state.per_edge_losses.get(config_stem, 0) >= _per_edge_loss_cap:
            _fire_edge_kill(config_stem, state.per_edge_losses[config_stem])

    # Global consecutive loss tracking (all buckets contribute to streak)
    if pnl < 0:
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0

    # Safety #14: per-bot daily loss cap — main + longshot only; discovery excluded
    # Discovery losses are capped by their own bucket halt; main halt must not be
    # contaminated by discovery variance (that defeats the whole point of the bucket).
    if risk_bucket == "discovery":
        # Still run daily reset logic, but skip the per-bot pnl accumulation
        today = time.strftime('%Y-%m-%d', time.gmtime())
        if state.last_pnl_reset_day != today:
            log.info("DAILY_RESET: resetting daily_pnl_by_bot (was %s, now %s)", state.last_pnl_reset_day, today)
            state.daily_pnl_by_bot = {}
            state.last_pnl_reset_day = today
            if state.halt and state.halt_reason and 'bot_daily_loss_cap' in state.halt_reason:
                log.info("DAILY_RESET: clearing bot_daily_loss_cap halt from prior day")
                state.halt = False
                state.halt_reason = None
            for _lock_name in ("kill_switch.lock", "session_halt.lock"):
                _lock_path = _DATA_DIR / _lock_name
                if _lock_path.exists():
                    try:
                        _archive_dir = _DATA_DIR / "lock_archive"
                        _archive_dir.mkdir(parents=True, exist_ok=True)
                        _lock_path.rename(_archive_dir / f"{_lock_name}.{int(time.time())}")
                        log.info("DAILY_RESET: archived stale %s", _lock_name)
                    except Exception as _le:
                        log.warning("DAILY_RESET: failed to archive %s: %s", _lock_name, _le)
        # Discovery daily loss cap check (independent of main halt)
        if not state.discovery_halt:
            _max_disc_loss = _discovery_max_daily_loss()
            if state.discovery_daily_loss <= -_max_disc_loss:
                state.discovery_halt = True
                write_discovery_halt_marker(
                    reason=f"discovery_daily_loss_cap daily_loss={state.discovery_daily_loss:.2f} cap={_max_disc_loss:.2f}",
                    daily_loss_usd=abs(state.discovery_daily_loss),
                )
                log.warning(
                    "DISCOVERY_HALT: daily_loss=%.2f >= cap=%.2f (main trading UNAFFECTED)",
                    abs(state.discovery_daily_loss), _max_disc_loss,
                )
        # C6: discovery losses do NOT trip session drawdown halt — return early
        return

    if bot_id is None:
        import socket
        bot_id = os.environ.get("V40_BOT_ID") or socket.gethostname()
    # UTC-midnight daily reset — clears accumulated PnL and bot_daily_loss_cap halts
    today = time.strftime('%Y-%m-%d', time.gmtime())
    if state.last_pnl_reset_day != today:
        log.info("DAILY_RESET: resetting daily_pnl_by_bot (was %s, now %s)", state.last_pnl_reset_day, today)
        state.daily_pnl_by_bot = {}
        state.last_pnl_reset_day = today
        if state.halt and state.halt_reason and 'bot_daily_loss_cap' in state.halt_reason:
            log.info("DAILY_RESET: clearing bot_daily_loss_cap halt from prior day")
            state.halt = False
            state.halt_reason = None
        # Also archive persistent lock files so should_block_trades() stops blocking
        for _lock_name in ("kill_switch.lock", "session_halt.lock"):
            _lock_path = _DATA_DIR / _lock_name
            if _lock_path.exists():
                try:
                    _archive_dir = _DATA_DIR / "lock_archive"
                    _archive_dir.mkdir(parents=True, exist_ok=True)
                    _lock_path.rename(_archive_dir / f"{_lock_name}.{int(time.time())}")
                    log.info("DAILY_RESET: archived stale %s", _lock_name)
                except Exception as _le:
                    log.warning("DAILY_RESET: failed to archive %s: %s", _lock_name, _le)
    state.daily_pnl_by_bot[bot_id] = state.daily_pnl_by_bot.get(bot_id, 0.0) + pnl
    _bot_daily_loss_pct = state.daily_pnl_by_bot[bot_id] / cfg.bankroll_session_start if cfg.bankroll_session_start > 0 else 0.0
    if _bot_daily_loss_pct < -cfg.bot_daily_loss_cap_pct and not state.halt:
        state.halt = True
        state.halt_reason = f"bot_daily_loss_cap bot_id={bot_id} pnl={state.daily_pnl_by_bot[bot_id]:.2f}"
        _drawdown_pct = abs(_bot_daily_loss_pct)
        write_kill_marker("bot_daily_loss_cap", drawdown_pct=_drawdown_pct, bankroll_at_halt=cfg.bankroll_session_start + state.session_pnl)
        _write_session_halt_marker(state.halt_reason)
        log.warning("HALT: bot_daily_loss_cap bot_id=%s pnl=%.2f", bot_id, state.daily_pnl_by_bot[bot_id])

    # C6: session drawdown halt — ONLY on main_session_pnl, NOT total session_pnl
    # This is the dual-halt check (line ~245): longshot losses don't trip it.
    if state.main_session_pnl <= -cfg.session_drawdown_stop_pct * cfg.bankroll_session_start and not state.halt:
        state.halt = True
        state.halt_reason = f"session_drawdown main_pnl={state.main_session_pnl:.2f}"
        _dd_pct = abs(state.main_session_pnl) / cfg.bankroll_session_start if cfg.bankroll_session_start > 0 else 0.0
        write_kill_marker("session_drawdown", drawdown_pct=_dd_pct, bankroll_at_halt=cfg.bankroll_session_start + state.session_pnl)
        _write_session_halt_marker(state.halt_reason)
        log.warning(
            "HALT: session_drawdown main_pnl=%.2f threshold=%.1f%% (longshot_pnl=%.2f excluded)",
            state.main_session_pnl, cfg.session_drawdown_stop_pct * 100, state.longshot_session_pnl,
        )

    # Longshot daily loss cap (independent of main halt)
    # Note: discovery bucket has already returned above via early-return path
    if risk_bucket == "longshot" and not state.longshot_halt:
        _max_ls_loss = _longshot_max_daily_loss()
        if state.longshot_daily_loss <= -_max_ls_loss:
            state.longshot_halt = True
            write_longshot_halt_marker(
                reason=f"longshot_daily_loss_cap daily_loss={state.longshot_daily_loss:.2f} cap={_max_ls_loss:.2f}",
                daily_loss_usd=abs(state.longshot_daily_loss),
            )
            log.warning(
                "LONGSHOT_HALT: daily_loss=%.2f >= cap=%.2f (main trading UNAFFECTED)",
                abs(state.longshot_daily_loss), _max_ls_loss,
            )


def _fire_edge_kill(config_stem: str, loss_count: int) -> None:
    """C7: write per-edge kill marker when consecutive losses >= 5."""
    _EDGE_KILLS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _EDGE_KILLS_DIR / f"{config_stem}.lock"
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    marker = {
        "killed": True,
        "config_stem": config_stem,
        "consecutive_losses": loss_count,
        "killed_at": _dt.now(tz=_tz.utc).isoformat(),
        "clear_instructions": "Delete this file after manual review + 24h cooldown to re-enable edge.",
    }
    lock_path.write_text(_json.dumps(marker, indent=2))
    log.warning("EDGE_KILL: %s after %d consecutive losses", config_stem, loss_count)


def is_edge_killed(stem: str) -> bool:
    """C7: return True if this edge has a per-edge kill marker."""
    lock_path = _EDGE_KILLS_DIR / f"{stem}.lock"
    if not lock_path.exists():
        return False
    import json as _json
    try:
        data = _json.loads(lock_path.read_text())
        return bool(data.get("killed", True))
    except Exception:
        return True  # fail-closed on corrupt lock


def confidence_multiplier(p_lo: float | None, p_hi: float | None) -> float:
    """MAPIE conformal-width bucket → per-trade sizing multiplier."""
    if p_lo is None or p_hi is None:
        return 1.0
    width = p_hi - p_lo
    if width < 0.10:
        return 2.5
    if width < 0.20:
        return 1.5
    if width < 0.30:
        return 1.0
    if width < 0.40:
        return 0.5
    return 0.0


def stella_state_multiplier(state: PortfolioState, cfg: RiskConfig) -> float:
    """GREEN/YELLOW/RED/BLACK drawdown+loss-streak state machine → sizing multiplier.

    Safety #10: BLACK state also fires halt + write_kill_marker so callers
    cannot silently ignore a 0.0 multiplier.
    """
    if state.halt:
        return 0.0
    bankroll = cfg.bankroll_session_start if cfg.bankroll_session_start > 0 else 100.0
    # Use main_session_pnl so longshot variance doesn't drag stella into RED/BLACK
    pnl_pct = state.main_session_pnl / bankroll
    losses = state.consecutive_losses
    # 2026-05-25: stella thresholds env-configurable for longshot survival mode.
    # At 29% WR longshots, P(8 consecutive losses) = 6.5% — fires too often at default.
    _black_pct = float(os.environ.get("V40_STELLA_BLACK_PCT", "0.10"))
    _black_loss = int(os.environ.get("V40_STELLA_BLACK_LOSS_STREAK", "8"))
    _red_pct = float(os.environ.get("V40_STELLA_RED_PCT", "0.05"))
    _red_loss = int(os.environ.get("V40_STELLA_RED_LOSS_STREAK", "5"))
    _yellow_pct = float(os.environ.get("V40_STELLA_YELLOW_PCT", "0.025"))
    _yellow_loss = int(os.environ.get("V40_STELLA_YELLOW_LOSS_STREAK", "3"))
    if pnl_pct <= -_black_pct or losses >= _black_loss:
        # BLACK — set halt + persist lock file so restart doesn't wipe it
        if not state.halt:
            from v40.risk_kill_switch import write_kill_marker, _write_session_halt_marker  # noqa: PLC0415
            state.halt = True
            state.halt_reason = f"stella_black pnl_pct={pnl_pct:.3f} losses={losses}"
            write_kill_marker("stella_black", drawdown_pct=abs(pnl_pct), bankroll_at_halt=bankroll + state.session_pnl)
            _write_session_halt_marker(state.halt_reason)
            log.warning("HALT: stella_black pnl_pct=%.3f consecutive_losses=%d", pnl_pct, losses)
        return 0.0  # BLACK — halt zone
    if pnl_pct <= -_red_pct or losses >= _red_loss:
        return 0.5  # RED
    if pnl_pct <= -_yellow_pct or losses >= _yellow_loss:
        return 0.75  # YELLOW
    return 1.0  # GREEN
