#!/usr/bin/env python3
"""
Extended sizing simulation: 20k grid, 10k MC, walk-forward.
Tests current config vs proposed small-bet config vs grid optimum.
"""
import sys
sys.path.insert(0, '/home/nick/kalshi-trader')
sys.path.insert(0, '/home/nick/kalshi-trader/scripts/backtests')

import sqlite3, math, time, pathlib
import numpy as np
from dataclasses import dataclass
from typing import Sequence

DB = pathlib.Path('/home/nick/kalshi-trader/kalshi_trader_v35.db')

START_BANK = 187.0   # approximate real starting balance for today's run
RNG_SEED   = 20260419
SIZING_LHS  = 20_000
MC_BOOTSTRAP = 10_000
WALK_FORWARD_SPLIT = 0.70
MIN_FIRE = 40

CURRENT   = dict(max_price=0.98, vwap_floor=0.85, bet_frac=0.40, bet_pct=0.30)
PROPOSED  = dict(max_price=0.98, vwap_floor=0.85, bet_frac=0.15, bet_pct=0.15)  # small bets

@dataclass
class Trade:
    opened_at: str
    ticker: str
    entry_price: float
    contracts_live: int
    cost_live: float
    pnl_live: float
    outcome: str
    vwap_signal: str
    vwap_conf: float
    kelly_frac: float | None

    @property
    def series(self):
        for suffix in ['-26APR', '-26MAR', '-26FEB', '-26JAN']:
            idx = self.ticker.find(suffix)
            if idx > 0:
                return self.ticker[:idx]
        return self.ticker.split('-')[0]

    @property
    def per_contract_pnl(self):
        if self.contracts_live <= 0: return 0.0
        return self.pnl_live / self.contracts_live


def load_trades():
    con = sqlite3.connect(DB)
    # Deduplicate by client_order_id — DB double-logs every trade
    rows = con.execute("""
        SELECT opened_at, ticker, entry_price, contracts, cost, pnl, outcome,
               COALESCE(vwap_signal,'no_data'), COALESCE(vwap_conf, 0.0), kelly_frac,
               client_order_id
        FROM trades
        WHERE mode='live' AND outcome IN ('win','loss')
        ORDER BY opened_at ASC
    """).fetchall()
    con.close()
    seen = set()
    trades = []
    for row in rows:
        cid = row[10]
        if cid and cid in seen:
            continue
        if cid:
            seen.add(cid)
        trades.append(Trade(*row[:10]))
    print(f"Loaded {len(trades)} deduplicated live trades (from {len(rows)} raw rows).")
    return trades


def size_contracts(balance, price, bet_frac, bet_pct, max_order=999999):
    effective = min(balance, 999999)
    pct_cap = effective * bet_pct
    base_spend = min(effective * bet_frac, pct_cap)
    spend = min(base_spend, max_order)
    return max(1, int(spend / max(price, 0.01)))


def replay(trades, cfg, start_bank=START_BANK, max_order=999999):
    bank = start_bank
    pnl_series = []
    max_drawdown = 0.0
    peak = bank
    ruin = False
    for t in trades:
        if t.entry_price > cfg['max_price']:
            continue
        if t.vwap_signal == 'confirm' and t.vwap_conf < cfg['vwap_floor']:
            continue
        frac_used = t.kelly_frac if t.kelly_frac is not None else cfg['bet_frac']
        scaled_frac = min(frac_used * (cfg['bet_frac'] / 0.40), cfg['bet_frac'])
        contracts = size_contracts(bank, t.entry_price, scaled_frac, cfg['bet_pct'], max_order)
        trade_pnl = contracts * t.per_contract_pnl
        bank += trade_pnl
        pnl_series.append(trade_pnl)
        if bank > peak: peak = bank
        dd = (peak - bank) / peak if peak > 0 else 0
        if dd > max_drawdown: max_drawdown = dd
        if bank < 10: ruin = True; break
    arr = np.asarray(pnl_series, dtype=np.float64) if pnl_series else np.zeros(1)
    total = float(arr.sum())
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    sharpe = (mean / std * math.sqrt(len(arr))) if std > 0 else 0.0
    return dict(fired=len(pnl_series), total_pnl=total, mean_pnl=mean, std=std,
                sharpe=sharpe, final_bank=bank, max_drawdown=max_drawdown,
                ruin=ruin, pnl_series=arr.tolist())


def mc_bootstrap(pnl_series, n_iter, rng):
    arr = np.asarray(pnl_series, dtype=np.float64)
    n = len(arr)
    if n < 2: return dict(total_ci=(0,0), sharpe_ci=(0,0), ruin_pct=0)
    totals = np.empty(n_iter)
    sharpes = np.empty(n_iter)
    ruins = 0
    start = START_BANK
    for i in range(n_iter):
        idx = rng.integers(0, n, size=n)
        sample = arr[idx]
        # path simulation for ruin
        bank = start
        ruined = False
        for p in sample:
            bank += p
            if bank < 10: ruined = True; break
        if ruined: ruins += 1
        totals[i] = sample.sum()
        s = sample.std(ddof=1)
        sharpes[i] = sample.mean()/s*math.sqrt(n) if s > 0 else 0
    ci = lambda a: (float(np.percentile(a,2.5)), float(np.percentile(a,97.5)))
    return dict(total_ci=ci(totals), sharpe_ci=ci(sharpes), ruin_pct=ruins/n_iter*100)


def walk_forward(trades, cfg):
    n = len(trades)
    split = int(n * WALK_FORWARD_SPLIT)
    train = replay(trades[:split], cfg)
    test  = replay(trades[split:], cfg)
    stab = test['sharpe']/train['sharpe'] if train['sharpe'] > 0 else 0
    return dict(train_sh=train['sharpe'], test_sh=test['sharpe'],
                train_pnl=train['total_pnl'], test_pnl=test['total_pnl'],
                stability=stab)


def latin_hypercube(n, bounds, rng):
    d = len(bounds)
    samples = np.empty((n, d))
    for j, (lo, hi) in enumerate(bounds):
        perm = rng.permutation(n)
        u = (perm + rng.random(n)) / n
        samples[:, j] = lo + u * (hi - lo)
    return samples


def full_summary(trades, cfg, label, rng):
    r = replay(trades, cfg)
    wf = walk_forward(trades, cfg)
    boot = mc_bootstrap(r['pnl_series'], MC_BOOTSTRAP, rng)
    print(f"\n{'='*60}")
    print(f"SCENARIO: {label}")
    print(f"  cfg: bet_frac={cfg['bet_frac']:.2f}  bet_pct={cfg['bet_pct']:.2f}  "
          f"max_price={cfg['max_price']:.3f}  vwap_floor={cfg['vwap_floor']:.2f}")
    print(f"  Trades fired : {r['fired']} / {len(trades)}")
    print(f"  Final bank   : ${r['final_bank']:.2f}  (start ${START_BANK:.2f})")
    print(f"  Total PnL    : ${r['total_pnl']:.2f}")
    print(f"  Sharpe       : {r['sharpe']:.3f}")
    print(f"  Max Drawdown : {r['max_drawdown']*100:.1f}%")
    print(f"  Ruin (bank<10): {'YES' if r['ruin'] else 'NO'}")
    print(f"  MC 95% CI PnL: (${boot['total_ci'][0]:.2f}, ${boot['total_ci'][1]:.2f})")
    print(f"  MC 95% CI Sh : ({boot['sharpe_ci'][0]:.3f}, {boot['sharpe_ci'][1]:.3f})")
    print(f"  MC Ruin %    : {boot['ruin_pct']:.1f}%")
    print(f"  Walk-forward : train_sh={wf['train_sh']:.3f}  test_sh={wf['test_sh']:.3f}  "
          f"stab={wf['stability']:.3f}")
    print(f"  Walk-forward PnL: train=${wf['train_pnl']:.2f}  test=${wf['test_pnl']:.2f}")
    return dict(label=label, cfg=cfg, r=r, wf=wf, boot=boot)


def main():
    rng = np.random.default_rng(RNG_SEED)
    trades = load_trades()
    print(f"Time range: {trades[0].opened_at[:10]} .. {trades[-1].opened_at[:10]}")
    print(f"Win rate: {sum(1 for t in trades if t.outcome=='win')}/{len(trades)} = "
          f"{100*sum(1 for t in trades if t.outcome=='win')/len(trades):.1f}%")
    print(f"Series breakdown:")
    from collections import Counter
    sc = Counter(t.series for t in trades)
    for s,n in sc.most_common():
        wins = sum(1 for t in trades if t.series==s and t.outcome=='win')
        print(f"  {s:15s} {n:3d} trades  {100*wins/n:.1f}% WR")

    # 1) Current vs Proposed
    full_summary(trades, CURRENT,  "CURRENT  (bet_frac=0.40)", rng)
    full_summary(trades, PROPOSED, "PROPOSED (bet_frac=0.15)", rng)

    # 2) 20k sizing LHS sweep
    print(f"\n{'='*60}")
    print(f"SIZING LHS SWEEP ({SIZING_LHS} samples, bet_frac x bet_pct)")
    bounds = [(0.05, 0.50), (0.05, 0.60)]
    samples = latin_hypercube(SIZING_LHS, bounds, rng)
    results = []
    for i, (bf, bp) in enumerate(samples):
        cfg = dict(bet_frac=float(bf), bet_pct=float(bp),
                   max_price=CURRENT['max_price'], vwap_floor=CURRENT['vwap_floor'])
        r = replay(trades, cfg)
        if r['fired'] >= MIN_FIRE and not math.isnan(r['sharpe']) and not r['ruin']:
            results.append(dict(cfg=cfg, **{k:v for k,v in r.items() if k!='pnl_series'}))
    results_sh  = sorted(results, key=lambda x: x['sharpe'], reverse=True)
    results_pnl = sorted(results, key=lambda x: x['total_pnl'], reverse=True)

    print(f"Valid configs (no ruin, fired>={MIN_FIRE}): {len(results)}/{SIZING_LHS}")
    print(f"\nTop 10 by Sharpe:")
    print(f"  {'bet_frac':>8} {'bet_pct':>8} {'fired':>6} {'total_pnl':>10} {'sharpe':>8} {'max_dd':>7} {'final_bank':>11}")
    for r in results_sh[:10]:
        c = r['cfg']
        print(f"  {c['bet_frac']:8.3f} {c['bet_pct']:8.3f} {r['fired']:6d} "
              f"${r['total_pnl']:9.2f} {r['sharpe']:8.3f} {r['max_drawdown']*100:6.1f}% "
              f"${r['final_bank']:10.2f}")

    print(f"\nTop 10 by PnL (no ruin):")
    print(f"  {'bet_frac':>8} {'bet_pct':>8} {'fired':>6} {'total_pnl':>10} {'sharpe':>8} {'max_dd':>7} {'final_bank':>11}")
    for r in results_pnl[:10]:
        c = r['cfg']
        print(f"  {c['bet_frac']:8.3f} {c['bet_pct']:8.3f} {r['fired']:6d} "
              f"${r['total_pnl']:9.2f} {r['sharpe']:8.3f} {r['max_drawdown']*100:6.1f}% "
              f"${r['final_bank']:10.2f}")

    # 3) Full summary for grid-optimal Sharpe config
    if results_sh:
        best_sh = results_sh[0]
        full_summary(trades, best_sh['cfg'], f"GRID OPTIMUM (Sharpe)", rng)
    if results_pnl:
        best_pnl = results_pnl[0]
        full_summary(trades, best_pnl['cfg'], f"GRID OPTIMUM (PnL)", rng)

    # 4) Drawdown analysis at current vs proposed
    print(f"\n{'='*60}")
    print("DRAWDOWN DISTRIBUTION (current vs proposed, 10k MC paths)")
    for label, cfg in [("CURRENT", CURRENT), ("PROPOSED", PROPOSED)]:
        r = replay(trades, cfg)
        arr = np.asarray(r['pnl_series'])
        dds = []
        for _ in range(10_000):
            idx = rng.integers(0, len(arr), size=len(arr))
            sample = arr[idx]
            bank = START_BANK; peak = bank; max_dd = 0
            for p in sample:
                bank += p
                if bank > peak: peak = bank
                dd = (peak-bank)/peak if peak > 0 else 0
                if dd > max_dd: max_dd = dd
            dds.append(max_dd)
        dds = np.array(dds)
        print(f"  {label}: median_dd={np.median(dds)*100:.1f}%  "
              f"p95_dd={np.percentile(dds,95)*100:.1f}%  "
              f"p99_dd={np.percentile(dds,99)*100:.1f}%")

if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nDone in {time.time()-t0:.1f}s")
