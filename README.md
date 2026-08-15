# Kalshi Algorithmic Trading System — Architecture Showcase

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Architecture Showcase](https://img.shields.io/badge/architecture-showcase-blueviolet)]()

> **Production algorithmic trading system for Kalshi prediction markets. 6 months live execution with positive PnL. This repo demonstrates architecture only — strategy alpha is not included.**

## System Overview

| Component | Description | File |
|-----------|-------------|------|
| **Alpha Factory v3** | Multi-timeframe alpha discovery with regime detection | `core/alpha_factory_v3.py` |
| **Evolution Engine** | Genetic algorithm for strategy parameter optimization | `core/evolution_engine.py` |
| **Bias Harvester** | Market microstructure bias extraction | `core/bias_harvester.py` |
| **Risk Kill Switch** | Real-time position/portfolio risk controls | `risk/risk_kill_switch.py` |
| **Backtest Framework** | 50k+ historical trade simulation with walk-forward validation | `backtest/` |

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

## Key Results (Sanitized)

| Metric | Value |
|--------|-------|
| **Total Trades** | 50,000+ |
| **Win Rate** | 52-58% (regime-dependent) |
| **Sharpe Ratio** | 1.8-2.4 |
| **Max Drawdown** | <8% |
| **Profit Factor** | 1.6-2.1 |
| **Live Execution** | 6 months (2026 Q1-Q2) |

*Full interactive dashboard: `results/backtest_report.html` (generate with `python backtest/backtest_spot_signals.py`)*

## Quick Start

```bash
# Clone
git clone https://github.com/mrnicholasbcarter-code/kalshi-showcase.git
cd kalshi-showcase

# Install dependencies
pip install -r requirements.txt

# Configure (copy example and add your Kalshi credentials)
cp config/config.example.yaml config/local.yaml
# Edit config/local.yaml with your API keys

# Run backtest (uses cached data)
python backtest/backtest_spot_signals.py --config config/backtest.example.yaml

# View results
open results/backtest_report.html
```

## Configuration

Required environment variables (see `.env.example`):
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
│   ├── risk_kill_switch.py  # Real-time position limits
│   ├── hrp.py               # Hierarchical Risk Parity
│   ├── kelly.py             # Kelly criterion sizing
│   └── v40_risk.py          # V4.0 risk framework
├── backtest/                # Backtest framework
│   ├── backtest_spot_signals.py
│   ├── backtest_v35_with_risk.py
│   └── sizing_sim.py        # Position sizing simulation
├── strategies/              # Strategy implementations
│   ├── alpha_engine.py
│   ├── alpha_research.py
│   └── hft_candidates.py
├── config/                  # Configuration templates
├── results/                 # Backtest output (gitignored)
└── v40/                     # V4.0 experimental
```

## Security

- **No API keys, private keys, or tokens in this repo**
- `.env.example` shows required variables (fill locally)
- Database files (`*.db`) are gitignored
- Strategy-specific alpha logic is abstracted — this is an architecture showcase

## License

MIT License — Architecture showcase only. Strategy IP not included.

---

**Built by** [Nicholas Carter](https://github.com/mrnicholasbcarter-code) — 25 years shipping systems at GM OnStar, Deloitte, BCBS Michigan, Mad Mobile/Stäubli, and now AI orchestration.
