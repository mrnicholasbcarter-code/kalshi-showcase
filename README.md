# Kalshi Trading System — Architecture Showcase

> Production-grade algorithmic trading system for Kalshi 15-minute prediction markets. This repository demonstrates the system architecture, backtest framework, risk management, and performance results — without proprietary alpha.

## Overview

This system trades Kalshi's 15-minute binary option markets (crypto, weather, sports) using a multi-strategy approach with rigorous risk controls. The showcase includes:

- **Core Engine**: Autonomous strategy factory with research → code → test → simulate → promote pipeline
- **Backtest Framework**: Walk-forward validation with Monte Carlo sizing simulation and volatility regime analysis
- **Risk Management**: Fractional Kelly sizing, HRP portfolio allocation, multi-tier kill-switch (Stella), intraday/weekly drawdown limits
- **Execution**: Maker-preference limit orders with slippage modeling, honest paper fills
- **Results Dashboard**: Interactive HTML report with PnL curves, Sharpe ratios, per-series breakdown

## Architecture

```
├── core/                    # Autonomous strategy factory
│   ├── alpha_factory_v3.py  # Research → Code → Test → Simulate → Promote loop
│   ├── engine.py            # Main trading engine (portfolio + venues + signals)
│   ├── evolution_engine.py  # Strategy evolution/genetic optimization
│   └── bias_harvester.py    # Market microstructure bias detection
│
├── backtest/                # Validation framework
│   ├── backtest_spot_signals.py   # Vol regime + momentum validation
│   ├── backtest_v35_with_risk.py  # Full backtest with risk controls
│   └── sizing_sim.py              # 20k grid + 10k Monte Carlo sizing
│
├── risk/                    # Risk management
│   ├── v40_risk.py          # Deterministic risk rails (veto/shrink ML trades)
│   ├── risk_kill_switch.py  # Intraday/weekly kill-switch with persistence
│   ├── kelly.py             # Fractional Kelly portfolio allocation
│   └── hrp.py               # HRP / CVaR-aware allocation
│
├── strategies/              # Strategy specifications
│   └── hft_candidates.py    # HFT candidate families (maker spread, quote fade, etc.)
│
├── config/                  # Configuration
│   ├── config.example.yaml  # Full system config template
│   └── config_loader.py     # YAML schema validation + engine bootstrap
│
├── results/                 # Performance reports
│   └── backtest_report.html # Interactive backtest dashboard
│
├── .env.example             # Environment variable template
└── .gitignore               # Excludes secrets, databases, logs
```

## Key Features

| Component | Description |
|-----------|-------------|
| **Alpha Factory V3** | Autonomous loop: scrapes opportunities → generates hypotheses → writes strategies → backtests → promotes to live |
| **Vol-Regime Adaptive** | Strategies adjust price thresholds based on real-time volatility regime (low/normal/high/extreme) |
| **Fractional Kelly** | Portfolio-level sizing with 25% Kelly fraction, HRP correlation-aware allocation |
| **Stella Kill-Switch** | 3-tier (yellow/red/black) drawdown + loss-streak protection with persisted halt state |
| **Maker Preference** | Limit orders with rebate capture; slippage modeled via Binance spot reference |
| **Walk-Forward OOS** | Train/test split with drop-best-day robustness check |

## Backtest Results (Paper Trading)

| Metric | Value |
|--------|-------|
| **Total PnL** | +$860.31 |
| **Total Trades** | 5,739 |
| **Win Rate** | 88.0% |
| **Daily Sharpe** | 5.12 |
| **Profit Factor** | 1.27 |
| **Max Drawdown** | ~18% (daily) |
| **Period** | Mar 28 – May 26, 2026 (23 days) |

[View Interactive Dashboard →](results/backtest_report.html)

### Per-Series Performance

| Series | Trades | PnL | Win Rate |
|--------|--------|-----|----------|
| KXXRP15M | 1,015 | +$483.60 | 90.3% |
| KXHYPE15M | 741 | +$482.78 | 89.6% |
| KXDOGE15M | 836 | +$177.55 | 88.6% |
| KXETH15M | 954 | +$115.00 | 87.6% |
| KXBNB15M | 525 | -$13.27 | 87.8% |
| KXSOL15M | 847 | -$26.81 | 86.9% |
| KXBTC15M | 757 | -$367.22 | 83.9% |

*Note: BTC/SOL/BNB show negative PnL despite high win rates due to adverse selection on large losses. This informed the vol-regime adaptive thresholds in production.*

## Quick Start

```bash
# Clone and setup
git clone https://github.com/yourusername/kalshi-showcase.git
cd kalshi-showcase
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt  # see below

# Configure (copy templates, fill in your keys)
cp .env.example .env
cp config/config.example.yaml config/config.yaml
# Edit .env with your Kalshi API credentials

# Run backtest
python3 backtest/backtest_spot_signals.py
python3 backtest/backtest_v35_with_risk.py

# Run paper trading
python3 core/engine.py --config config/config.yaml
```

### Requirements

```
pyyaml
requests
numpy
sqlite3 (stdlib)
```

## Configuration

The system uses YAML configuration with `${ENV_VAR}` substitution:

```yaml
# config/config.yaml (from config.example.yaml)
settings:
  mode: paper                    # paper | live
  bankroll_usd: 100.0
  fee_rate: 0.07

venues:
  kalshi:
    config:
      api_key: ${KALSHI_API_KEY}
      private_key: ${KALSHI_PRIVATE_KEY}
      key_id: ${KALSHI_KEY_ID}

risk:
  max_portfolio_drawdown_pct: 0.10
  stella:
    black_pct: 0.10
    black_loss_streak: 8
```

Environment variables (see `.env.example`):
- `KALSHI_API_KEY`, `KALSHI_PRIVATE_KEY`, `KALSHI_KEY_ID` — Required for live trading
- `BINANCE_API_KEY`, `BINANCE_API_SECRET` — Optional, for spot price reference
- `V40_STELLA_*` — Kill-switch thresholds (have sensible defaults)

## Security

- **No secrets in repo**: `.env`, `*.db`, `*.log` are gitignored
- **Config templates only**: Use `.env.example` and `config.example.yaml` as starting points
- **Paper-first**: Default mode is paper trading; live requires explicit config change
- **Kill-switch persisted**: Halt state survives restarts via lock files

## Disclaimer

This is an **architecture showcase** for portfolio demonstration. It contains:

- ✅ Real system architecture and components
- ✅ Actual backtest results from paper trading
- ✅ Production risk management code
- ❌ No live API keys or credentials
- ❌ No proprietary alpha signals or model weights
- ❌ No guaranteed future performance

Past performance does not guarantee future results. Trading involves substantial risk of loss.

## License

MIT License — See [LICENSE](LICENSE) for details.