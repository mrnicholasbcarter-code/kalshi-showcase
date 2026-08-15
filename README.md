# Kalshi Algorithmic Trading System — Architecture Showcase

> **Production algorithmic trading system for Kalshi prediction markets. 6 months live execution with positive PnL. This repo demonstrates architecture only — strategy alpha is not included.**

## System Overview

| Component | Description |
|-----------|-------------|
| **Alpha Factory v3** | Multi-timeframe alpha discovery with regime detection |
| **Evolution Engine** | Genetic algorithm for strategy parameter optimization |
| **Bias Harvester** | Market microstructure bias extraction |
| **Risk Kill Switch** | Real-time position/portfolio risk controls |
| **Backtest Framework** | 50k+ historical trade simulation with walk-forward validation |

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

```bash
# Install dependencies
pip install -r requirements.txt

# Run backtest (uses cached data)
python backtest/backtest_spot_signals.py --config config/backtest.example.yaml

# View results
open results/backtest_report.html
```

## Backtest Results (Sanitized)

| Metric | Value |
|--------|-------|
| Total Trades | 50,000+ |
| Win Rate | 52-58% (regime-dependent) |
| Sharpe Ratio | 1.8-2.4 |
| Max Drawdown | <8% |
| Profit Factor | 1.6-2.1 |

*Full interactive dashboard: `results/backtest_report.html`*

## Configuration

Copy `config/example.yaml` to `config/local.yaml` and fill in:
- Kalshi API credentials (not included)
- Risk parameters
- Strategy selection

## Security

- **No API keys, private keys, or tokens in this repo**
- `.env.example` shows required variables (fill locally)
- Database files (`*.db`) are gitignored
- Strategy-specific alpha logic is abstracted

## License

Architecture showcase only. Strategy IP not included.
