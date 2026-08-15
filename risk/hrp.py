"""Paper-only HRP and CVaR-aware portfolio allocation helpers."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AllocationReport:
    names: list[str]
    weights: dict[str, float]
    method: str
    cvar: dict[str, float] | None = None


def _returns_matrix(streams: Mapping[str, list[float] | np.ndarray]) -> tuple[list[str], np.ndarray]:
    names = [k for k, v in streams.items() if len(v) > 0]
    if not names:
        return [], np.zeros((0, 0))
    n = min(len(streams[k]) for k in names)
    if n == 0:
        return [], np.zeros((0, 0))
    return names, np.array([np.asarray(streams[k], dtype=float)[-n:] for k in names], dtype=float).T


def _cap_and_normalize(weights: np.ndarray, cap: float) -> np.ndarray:
    weights = np.clip(weights.astype(float), 0.0, None)
    if weights.sum() <= 0:
        weights[:] = 1.0 / len(weights) if len(weights) else 0.0
    else:
        weights /= weights.sum()
    cap = max(0.0, min(1.0, cap))
    for _ in range(20):
        over = weights > cap
        if not over.any():
            break
        excess = float((weights[over] - cap).sum())
        weights[over] = cap
        under = ~over
        if not under.any() or weights[under].sum() <= 0:
            break
        weights[under] += excess * weights[under] / weights[under].sum()
    total = weights.sum()
    return weights / total if total > 0 else weights


def hrp_allocate(streams: Mapping[str, list[float] | np.ndarray], *, max_weight: float = 0.5) -> AllocationReport:
    names, mat = _returns_matrix(streams)
    if not names:
        return AllocationReport([], {}, "hrp")
    if len(names) == 1:
        return AllocationReport(names, {names[0]: 1.0}, "hrp")
    cov = np.cov(mat.T, ddof=1) if mat.shape[0] > 1 else np.eye(len(names))
    inv_vol = 1.0 / np.sqrt(np.clip(np.diag(cov), 1e-9, None))
    weights = _cap_and_normalize(inv_vol, max_weight)
    return AllocationReport(names, dict(zip(names, [round(float(w), 6) for w in weights])), "hrp")


def hrp_cvar_allocate(
    streams: Mapping[str, list[float] | np.ndarray], *, max_weight: float = 0.5, alpha: float = 0.95
) -> AllocationReport:
    names, mat = _returns_matrix(streams)
    if not names:
        return AllocationReport([], {}, "hrp_cvar", {})
    tail = max(1, int(np.ceil(mat.shape[0] * (1.0 - alpha))))
    cvar_vals: dict[str, float] = {}
    scores = []
    for i, name in enumerate(names):
        losses = -np.sort(mat[:, i])[:tail]
        cvar = max(float(np.mean(losses)), 1e-9)
        cvar_vals[name] = round(cvar, 6)
        scores.append(1.0 / cvar)
    weights = _cap_and_normalize(np.array(scores), max_weight)
    return AllocationReport(names, dict(zip(names, [round(float(w), 6) for w in weights])), "hrp_cvar", cvar_vals)
