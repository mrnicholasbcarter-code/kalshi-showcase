# Kalshi Algorithmic Trading System — Architecture Showcase

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)

## Live track record (exchange-verified)

Period: 2026-03-20 to 2026-07-18 (UTC).
Markets traded: 3,566. Fills: 4,945.
Instrument mix: mostly 15-minute crypto markets (ETH, DOGE, XRP, SOL, BNB, HYPE, BTC) plus daily-high weather markets.

| Metric | Value |
|--------|-------|
| **Win rate** | 85.4% (3,045 wins / 519 losses) |
| **Average win** | $0.91 |
| **Average loss** | $5.99 |

**Win-rate by month**

| Month | Win rate |
|-------|----------|
| March 2026 | 94.9% |
| April 2026 | 90.9% |
| May 2026 | 95.6% |
| June 2026 | 66.3% |

**Win definition:** a market position whose exchange-reported realized P&L is > 0;
win rate = wins / (wins + losses).

**Raw-data SHA-256 (data not published):**
- `historical_positions`: `2dfabf2de1db3e6ef2fbf04e45986f3fd228e6c04d39a8dcf232500ad917d458`
- `historical_fills`: `80b3f8911edfcc239e540f1715ebaff8d148325e91580c86d166da604e44c614`

Machine-readable summary: [`results/live_track_record.json`](results/live_track_record.json)

Source: Kalshi exchange API (read-only), verified 2026-09-25.

---

## What the record taught me

A very high win rate on near-certain contracts is not the same as an edge — you can be right 85% of the time and still lose money if the losses are large enough relative to the wins. Here, an average win of $0.91 against an average loss of $5.99 means roughly seven wins are needed to absorb a single loss, leaving little margin for a bad streak. That is exactly why the risk layer in this repo exists: fractional Kelly sizing across the candidate pool (`risk/kelly.py`) caps exposure per position, Hierarchical Risk Parity allocation (`risk/hrp.py`) spreads risk across uncorrelated markets, and the intraday/weekly kill switch (`risk/risk_kill_switch.py`) halts new trades when drawdown thresholds are breached. The June 2026 regime change — win rate dropped from ~95% to 66% — is the clearest illustration of where those rules matter most. Without the kill switch and sizing constraints, that single month could have erased gains from the prior three.

---

## System Overview

| Component | Description | File |
|-----------|-------------|------|
| **Alpha Factory v3** | Multi-timeframe alpha discovery with regime detection | `core/alpha_factory_v3.py` |
| **Evolution Engine** | Genetic algorithm for strategy parameter optimization | `core/evolution_engine.py` |
| **Bias Harvester** | Market microstructure bias extraction | `core/bias_harvester.py` |
| **Risk Kill Switch** | Real-time intraday/weekly drawdown controls | `risk/risk_kill_switch.py` |
| **Kelly Sizing** | Fractional Kelly sizing across candidate pool | `risk/kelly.py` |
| **HRP Allocation** | Hierarchical Risk Parity portfolio optimizer | `risk/hrp.py` |
| **Backtest Framework** | Historical trade simulation with walk-forward validation | `backtest/` |

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Market Data    │────▶│  Alpha Factory   │────▶│  Evolution      │
│  Ingestion      │     │  (Regime-Aware)  │     │  Engine         │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
┌─────────────────┐     ┌──────────────────┐     ┌────────▼────────┐
│  Execution      │◀────│  Risk Manager    │◀────│  Portfolio      │
│  (Kalshi WS)    │     │  (Kill Switch)   │     │  Optimizer      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Quick Start

> **Note on backtest scripts:** `backtest/backtest_spot_signals.py` and
> `backtest/backtest_v35_with_risk.py` read from the operator's private SQLite
> trade database and **cannot run from this repo alone**. They are included to
> show architecture and query patterns only.

The Kelly and HRP math modules have **no private dependencies** and run on a
fresh clone with only `numpy` installed:

```bash
# Clone
git clone https://github.com/mrnicholasbcarter-code/kalshi-showcase.git
cd kalshi-showcase

# Create a clean venv and install dependencies
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Kelly sizing example (no private data needed):**

```python
from risk.kelly import portfolio_kelly_sizes

candidates = [
    {"domain": "crypto", "ticker": "ETHX",  "side": "yes", "p_win": 0.92, "price": 0.88},
    {"domain": "crypto", "ticker": "DOGEX", "side": "yes", "p_win": 0.78, "price": 0.70},
]
result = portfolio_kelly_sizes(
    candidates, bankroll=500.0, max_portfolio_usd=50.0
)
for r in result:
    print(f"{r.ticker}: kelly_fraction={r.kelly_fraction:.4f}  sized=${r.sized_usd:.2f}")
```

**HRP allocation example (no private data needed):**

```python
import numpy as np
from risk.hrp import hrp_allocate

streams = {
    "ETHX":  list(np.random.randn(60) * 0.02),
    "DOGEX": list(np.random.randn(60) * 0.03),
    "SOLX":  list(np.random.randn(60) * 0.025),
}
report = hrp_allocate(streams)
print(f"Method: {report.method}")
for name, w in report.weights.items():
    print(f"  {name}: {w:.4f}")
```

## Configuration

Required environment variables for live execution (see `.env.example`):
```bash
KALSHI_API_KEY_ID=your_key_id
KALSHI_PRIVATE_KEY_PATH=/path/to/private_key.pem
KALSHI_BASE_URL=https://api.elections.kalshi.com/trade-api/v2
```

## Project Structure

```
kalshi-showcase/
├── core/                    # Alpha generation engines
│   ├── alpha_factory_v3.py  # Multi-timeframe alpha + regime detection
│   ├── evolution_engine.py  # Genetic optimization
│   ├── bias_harvester.py    # Microstructure bias extraction
│   └── engine.py            # Main execution loop
├── risk/                    # Risk management
│   ├── risk_kill_switch.py  # Intraday/weekly kill switch
│   ├── hrp.py               # Hierarchical Risk Parity
│   ├── kelly.py             # Kelly criterion sizing
│   ├── risk_manager.py      # Risk manager integration
│   └── v40_risk.py          # V4.0 risk framework
├── backtest/                # Backtest framework (needs private DB)
│   ├── backtest_spot_signals.py
│   ├── backtest_v35_with_risk.py
│   └── sizing_sim.py        # Position sizing simulation
├── strategies/              # Strategy implementations
│   ├── alpha_engine.py
│   ├── alpha_research.py
│   └── hft_candidates.py
├── config/                  # Configuration templates
└── results/                 # Outputs
    ├── backtest_report.html # Paper-trading simulation (see note below)
    └── live_track_record.json
```

**`results/backtest_report.html`** — Paper-trading simulation (simulated fills),
2026-03-28 to 2026-05-26: not live results.

## Security

- **No API keys, private keys, or tokens in this repo**
- `.env.example` shows required variables (fill locally)
- Database files (`*.db`) are gitignored
- Strategy-specific alpha logic is abstracted — this is an architecture showcase

## License

MIT License — Architecture showcase only. Strategy IP not included.

---

**Built by** [Nicholas Carter](https://github.com/mrnicholasbcarter-code), AI infrastructure engineer.
