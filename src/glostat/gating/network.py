from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

# Gating Network — MOET A1 (IC-softmax + entropy regularization + per-expert caps).
# Sprint 1 PR #5: pure function, deterministic, no I/O after construction.

_DEFAULT_CONFIG: Final[Path] = (
    Path(__file__).resolve().parents[3] / "configs" / "gating.yaml"
)
_EPS: Final[float] = 1e-9


def default_config_path() -> Path:
    return _DEFAULT_CONFIG


@dataclass(frozen=True, slots=True)
class _GatingConfig:
    initial_ic: dict[str, float]
    weight_caps: dict[str, float]
    deferred_experts: frozenset[str]
    temperature: float
    entropy_lambda: float


class GatingNetwork:
    # WHY: load once at construction, derive weights many times — config is read-only.
    __slots__ = ("_cfg",)

    def __init__(self, config_path: Path | None = None) -> None:
        path = config_path or _DEFAULT_CONFIG
        self._cfg = _load_config(path)

    @property
    def temperature(self) -> float:
        return self._cfg.temperature

    @property
    def entropy_lambda(self) -> float:
        return self._cfg.entropy_lambda

    @property
    def deferred_experts(self) -> frozenset[str]:
        return self._cfg.deferred_experts

    def initial_ic(self, expert_name: str) -> float | None:
        return self._cfg.initial_ic.get(expert_name)

    def cap_for(self, expert_name: str) -> float:
        return self._cfg.weight_caps.get(expert_name, 1.0)

    def derive_weights(self, experts: list[str]) -> dict[str, float]:
        # Filter out unknown or deferred experts — they cannot vote in MVP.
        active = [e for e in experts if e in self._cfg.initial_ic
                  and e not in self._cfg.deferred_experts]
        if not active:
            return {}

        ic_vec = [self._cfg.initial_ic[e] for e in active]
        weights = _softmax(ic_vec, self._cfg.temperature)
        weights = _entropy_regularize(weights, self._cfg.entropy_lambda)
        weights = _apply_caps_and_renormalize(
            weights, [self._cfg.weight_caps.get(e, 1.0) for e in active]
        )
        return dict(zip(active, weights, strict=True))


def _load_config(path: Path) -> _GatingConfig:
    raw = yaml.safe_load(path.read_text("utf-8")) or {}
    initial_ic, deferred_ic = _parse_value_or_deferred(raw.get("initial_ic", {}))
    weight_caps, deferred_caps = _parse_value_or_deferred(raw.get("weight_caps", {}))
    softmax_cfg = raw.get("softmax", {}) or {}
    return _GatingConfig(
        initial_ic=initial_ic,
        weight_caps=weight_caps,
        deferred_experts=frozenset(deferred_ic | deferred_caps),
        temperature=float(softmax_cfg.get("temperature", 1.0)),
        entropy_lambda=float(softmax_cfg.get("entropy_lambda", 0.0)),
    )


def _parse_value_or_deferred(
    section: dict[str, object],
) -> tuple[dict[str, float], set[str]]:
    # WHY: schema allows either `name: 0.40` or `name: { value: 0.15, deferred_to: phase_2 }`.
    out: dict[str, float] = {}
    deferred: set[str] = set()
    for k, v in section.items():
        if isinstance(v, dict):
            val = v.get("value")
            if val is None:
                continue
            out[str(k)] = float(val)
            if v.get("deferred_to"):
                deferred.add(str(k))
        else:
            out[str(k)] = float(v)  # type: ignore[arg-type]
    return out, deferred


def _softmax(values: list[float], temperature: float) -> list[float]:
    if not values:
        return []
    t = max(temperature, _EPS)
    scaled = [v / t for v in values]
    m = max(scaled)  # numerical stability — subtract max before exp
    exps = [math.exp(v - m) for v in scaled]
    z = sum(exps) or _EPS
    return [e / z for e in exps]


def _entropy_regularize(weights: list[float], lam: float) -> list[float]:
    # MOET A1 — pull weights toward uniform by `lam` × KL_to_uniform penalty.
    # Implementation: w_i ← (1 − lam) × w_i + lam × (1/n). This is convex
    # interpolation between softmax distribution and the uniform — it dominates
    # multiplicative KL penalties for small n while preserving monotonicity.
    if not weights or lam <= 0.0:
        return weights
    n = len(weights)
    uniform = 1.0 / n
    return [(1.0 - lam) * w + lam * uniform for w in weights]


def _apply_caps_and_renormalize(
    weights: list[float], caps: list[float]
) -> list[float]:
    # WHY: when a weight exceeds its cap, hold it at the cap and redistribute the
    # excess proportionally across uncapped slots. Iterate because each pass may
    # push a previously-uncapped slot over its own cap.
    n = len(weights)
    if n == 0:
        return []
    out = list(weights)
    capped: set[int] = set()
    for _ in range(n + 1):
        # WHY: clamp + identify newly-violated indices.
        newly_capped: set[int] = set()
        for i in range(n):
            if i not in capped and out[i] > caps[i] + _EPS:
                out[i] = caps[i]
                newly_capped.add(i)
        capped |= newly_capped
        # WHY: renormalize uncapped mass to fill (1 − Σcapped).
        capped_sum = sum(out[i] for i in capped)
        free_sum = sum(out[i] for i in range(n) if i not in capped)
        target_free = max(0.0, 1.0 - capped_sum)
        if not newly_capped:
            # Stable — final renormalize over everything to absorb tiny drift.
            total = sum(out)
            if total <= _EPS:
                return [1.0 / n] * n
            return [w / total for w in out]
        if free_sum <= _EPS:
            # No uncapped headroom — distribute remaining target equally.
            for i in range(n):
                if i not in capped:
                    out[i] = target_free / max(1, n - len(capped))
        else:
            scale = target_free / free_sum
            for i in range(n):
                if i not in capped:
                    out[i] *= scale
    total = sum(out)
    return [w / max(total, _EPS) for w in out]


__all__ = ["GatingNetwork", "default_config_path"]
