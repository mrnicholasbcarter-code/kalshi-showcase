from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from v40.mining.schema import FeatureFilter, RuleHypothesisV1, rule_hypothesis_v1_from_dict


@dataclass(frozen=True)
class StrategySpec:
    name: str
    family: str
    side: str
    price_range: tuple[float, float]
    secs_to_close_range: tuple[int, int]
    filters: tuple[FeatureFilter, ...]
    max_hold_secs: int
    thesis: str

    def to_rule(self, *, series: tuple[str, ...] = ("*",), domain: str = "crypto") -> RuleHypothesisV1:
        raw: dict[str, Any] = {
            "schema_version": "rule_hypothesis_v1",
            "domain": domain,
            "universe": {"series": list(series), "min_volume_usd": 0, "min_secs_to_close": 0},
            "features": [f.feature for f in self.filters],
            "entry": {
                "side": self.side,
                "price_range": list(self.price_range),
                "secs_to_close_range": list(self.secs_to_close_range),
                "feature_filters": [{"feature": f.feature, "op": f.op, "value": f.value} for f in self.filters],
            },
            "exit": {"max_hold_secs": self.max_hold_secs},
            "sizing": {"kind": "fixed", "fraction": 0.005, "max_per_trade_usd": 1.0},
            "risk": {"max_drawdown_usd": 5.0, "max_open_positions": 1, "kill_switch": "paper_only"},
            "failure_modes": ["unfillable quote", "spread/slippage erases EV", "stale signal"],
            "provenance": {"generator": "hft_candidate_family", "source": self.family},
            "validation": {"min_examples": 50, "require_shadow_pass": True, "require_human_gate": True},
            "notes": self.thesis,
        }
        return rule_hypothesis_v1_from_dict(raw)


def strategy_specs() -> list[StrategySpec]:
    return [
        StrategySpec(
            name="maker_spread_rebate_capture",
            family="maker_spread",
            side="NO",
            price_range=(0.01, 0.99),
            secs_to_close_range=(120, 900),
            filters=(FeatureFilter("spread_bps", ">=", 80.0), FeatureFilter("book_depth", ">=", 50.0)),
            max_hold_secs=180,
            thesis="Paper maker candidate: wide spread with visible depth; exits fast if spread collapses.",
        ),
        StrategySpec(
            name="quote_fade_after_pull",
            family="quote_fade",
            side="NO",
            price_range=(0.01, 0.99),
            secs_to_close_range=(60, 900),
            filters=(FeatureFilter("quote_pull_depth_drop", ">=", 0.65), FeatureFilter("quote_pull_spread_ratio", ">=", 2.0)),
            max_hold_secs=120,
            thesis="Fade price after maker depth pull; stale/max-hold capped.",
        ),
        StrategySpec(
            name="stale_quote_cross_venue_scalp",
            family="stale_quote_cross_venue_scalp",
            side="YES",
            price_range=(0.01, 0.99),
            secs_to_close_range=(60, 900),
            filters=(FeatureFilter("book_age_ms", ">=", 2_000.0), FeatureFilter("cross_venue_residual", "<=", -0.03)),
            max_hold_secs=90,
            thesis="Scalp stale Kalshi quote against fresher external fair value; paper-only until fill realism passes.",
        ),
        StrategySpec(
            name="event_boundary_liquidity_scalp",
            family="event_boundary_scalp",
            side="NO",
            price_range=(0.01, 0.99),
            secs_to_close_range=(0, 300),
            filters=(FeatureFilter("event_boundary_secs", "<=", 60.0), FeatureFilter("spread_bps", ">=", 50.0)),
            max_hold_secs=60,
            thesis="Event-boundary microstructure scalp with strict stale/max-hold cap.",
        ),
    ]


def strategy_rules(series: tuple[str, ...] = ("*",), domain: str = "crypto") -> list[RuleHypothesisV1]:
    return [spec.to_rule(series=series, domain=domain) for spec in strategy_specs()]
