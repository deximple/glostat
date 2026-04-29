from __future__ import annotations

# Centralised error taxonomy. Keep narrow — most modules still raise stdlib types.


class GlostatError(RuntimeError):
    """Base class for all GLOSTAT-defined runtime errors."""


class ConfigError(GlostatError):
    """Raised when configuration / phase / consent prevents an operation.

    Use cases:
      - INV-GS-036: bigdata_client called in MVP phase
      - INV-GS-039: data_router asked to route to a phase-gated source
      - INV-GS-040: Phase 2/3 source requested without explicit consent flag
      - INV-GS-038: SEC EDGAR client missing User-Agent override
    """


class ExpertSkipError(GlostatError):
    """Raised by an Expert when its primary input is missing/empty/unusable.

    Hindcast (Sprint 4 PR #3) catches per-expert and either drops the verdict
    when ALL experts skip, or builds a partial verdict from surviving signals.
    Replaces the prior silent-zero behaviour (E_TIME t=0.0 on missing anchors,
    E_FUND_FLOW score=0.0 on INSUFFICIENT 13F coverage) so missing-data noise
    no longer dilutes Sharpe / AUC.
    """


__all__ = ["ConfigError", "ExpertSkipError", "GlostatError"]
