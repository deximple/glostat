from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from glostat.gating.network import GatingNetwork, default_config_path

# MOET A1 — IC-softmax + entropy regularization + per-expert caps.


def _write_cfg(tmp_path: Path, body: dict) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "gating.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


# ── derive_weights ─────────────────────────────────────────────────────────


def test_derive_weights_softmax_3_experts() -> None:
    g = GatingNetwork()
    w = g.derive_weights(["E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW"])
    assert set(w.keys()) == {"E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW"}
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)
    # E_FUNDAMENTAL has the highest IC (0.40) → highest weight.
    assert w["E_FUNDAMENTAL"] >= w["E_FUND_FLOW"] >= w["E_TIME"]


def test_softmax_with_temperature_1_0() -> None:
    g = GatingNetwork()
    assert g.temperature == 1.0
    w = g.derive_weights(["E_FUNDAMENTAL", "E_TIME"])
    # IC 0.40 vs 0.25 → softmax(0.40, 0.25) at T=1.0 with entropy reg.
    # softmax bare = (e^0.4, e^0.25)/(e^0.4+e^0.25) ≈ (0.5374, 0.4626)
    # entropy reg λ=0.1 → 0.9*w + 0.1*0.5 → ≈ (0.5337, 0.4663)
    # No caps active here (caps 0.40 / 0.25 → ≈ 0.6154 / 0.3846 after enforcement).
    assert w["E_FUNDAMENTAL"] > w["E_TIME"]
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_softmax_higher_temperature_flattens(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path, {
        "initial_ic": {"A": 1.0, "B": 0.0},
        "weight_caps": {"A": 1.0, "B": 1.0},
        "softmax": {"temperature": 100.0, "entropy_lambda": 0.0},
    })
    g = GatingNetwork(config_path=cfg)
    w = g.derive_weights(["A", "B"])
    # T very large → distribution approaches uniform.
    assert abs(w["A"] - w["B"]) < 0.01


def test_entropy_regularization_prevents_collapse(tmp_path: Path) -> None:
    # One IC dominates dramatically — entropy reg pulls toward uniform.
    cfg_no_reg = _write_cfg(tmp_path / "no", {
        "initial_ic": {"A": 5.0, "B": 0.0, "C": 0.0},
        "weight_caps": {"A": 1.0, "B": 1.0, "C": 1.0},
        "softmax": {"temperature": 1.0, "entropy_lambda": 0.0},
    })
    cfg_reg = _write_cfg(tmp_path / "reg", {
        "initial_ic": {"A": 5.0, "B": 0.0, "C": 0.0},
        "weight_caps": {"A": 1.0, "B": 1.0, "C": 1.0},
        "softmax": {"temperature": 1.0, "entropy_lambda": 0.5},
    })
    w_no = GatingNetwork(config_path=cfg_no_reg).derive_weights(["A", "B", "C"])
    w_reg = GatingNetwork(config_path=cfg_reg).derive_weights(["A", "B", "C"])
    # Without reg, A dominates near-completely; with reg, B+C share rises.
    assert w_no["A"] > w_reg["A"]
    assert w_reg["B"] > w_no["B"]


def test_weight_caps_clip(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path, {
        "initial_ic": {"A": 10.0, "B": 0.0},
        "weight_caps": {"A": 0.40, "B": 0.60},
        "softmax": {"temperature": 1.0, "entropy_lambda": 0.0},
    })
    g = GatingNetwork(config_path=cfg)
    w = g.derive_weights(["A", "B"])
    # Without caps, A would be ~99.99%. Cap drives it to exactly 0.40.
    assert w["A"] == pytest.approx(0.40, abs=1e-6)
    assert w["B"] == pytest.approx(0.60, abs=1e-6)


def test_renormalize_after_clip(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path, {
        "initial_ic": {"A": 0.5, "B": 0.5, "C": 0.5},
        "weight_caps": {"A": 0.20, "B": 1.0, "C": 1.0},
        "softmax": {"temperature": 1.0, "entropy_lambda": 0.0},
    })
    g = GatingNetwork(config_path=cfg)
    w = g.derive_weights(["A", "B", "C"])
    # A clipped to 0.20; remaining 0.80 split equally between B and C → 0.40 each.
    assert w["A"] == pytest.approx(0.20, abs=1e-6)
    assert w["B"] == pytest.approx(0.40, abs=1e-3)
    assert w["C"] == pytest.approx(0.40, abs=1e-3)
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_deferred_expert_excluded() -> None:
    # E_NARRATIVE is deferred_to: phase_2 in configs/gating.yaml — it must not
    # appear in the derived weights even when listed as input.
    g = GatingNetwork()
    w = g.derive_weights(["E_FUNDAMENTAL", "E_NARRATIVE", "E_TIME"])
    assert "E_NARRATIVE" not in w
    assert "E_FUNDAMENTAL" in w
    assert "E_TIME" in w
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_unknown_expert_silently_dropped() -> None:
    g = GatingNetwork()
    w = g.derive_weights(["E_FUNDAMENTAL", "E_NEVER_HEARD_OF"])
    assert "E_NEVER_HEARD_OF" not in w
    assert "E_FUNDAMENTAL" in w


def test_empty_input_returns_empty() -> None:
    g = GatingNetwork()
    assert g.derive_weights([]) == {}


def test_default_config_path_resolves() -> None:
    p = default_config_path()
    assert p.exists(), f"configs/gating.yaml missing at {p}"


def test_config_value_or_deferred_dict_form(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path, {
        "initial_ic": {"A": 0.40, "X": {"value": 0.10, "deferred_to": "phase_2"}},
        "weight_caps": {"A": 1.0, "X": {"value": 0.10, "deferred_to": "phase_2"}},
        "softmax": {"temperature": 1.0, "entropy_lambda": 0.0},
    })
    g = GatingNetwork(config_path=cfg)
    assert "X" in g.deferred_experts
    assert g.initial_ic("X") == 0.10
    assert g.cap_for("X") == 0.10
    assert g.derive_weights(["A", "X"]) == {"A": pytest.approx(1.0)}


def test_weight_for_e_fundamental_caps_to_40pct() -> None:
    g = GatingNetwork()
    # 3 experts → softmax of (0.40, 0.35, 0.25) with entropy reg + caps.
    w = g.derive_weights(["E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW"])
    # E_FUNDAMENTAL cap is 0.40 — final weight must respect it.
    assert w["E_FUNDAMENTAL"] <= 0.40 + 1e-6


def test_weights_are_deterministic() -> None:
    g = GatingNetwork()
    w1 = g.derive_weights(["E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW"])
    w2 = g.derive_weights(["E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW"])
    assert w1 == w2
