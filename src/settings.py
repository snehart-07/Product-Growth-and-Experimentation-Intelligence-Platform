"""Project-wide paths and data-source selection.

The pipeline in this repository has one source: the GA4 public sample (``PULSEIQ_SOURCE=ga4``).
The two experiment datasets are loaded directly by src/connectors/public_datasets.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALID_SOURCES = ("ga4",)


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    data: Path = ROOT / "data"
    raw_synthetic: Path = ROOT / "data" / "raw" / "synthetic"
    raw_ga4: Path = ROOT / "data" / "raw" / "ga4"
    processed_synthetic: Path = ROOT / "data" / "processed" / "synthetic"
    processed_ga4: Path = ROOT / "data" / "processed" / "ga4"
    metadata: Path = ROOT / "data" / "metadata"
    validation_synthetic: Path = ROOT / "data" / "validation" / "synthetic"
    sql: Path = ROOT / "sql"

    def raw(self, source: str) -> Path:
        return {"synthetic": self.raw_synthetic, "ga4": self.raw_ga4}[source]

    def processed(self, source: str) -> Path:
        return {"synthetic": self.processed_synthetic, "ga4": self.processed_ga4}[source]


PATHS = Paths()

SOURCE_LABELS = {
    "synthetic": {
        "label": "Synthetic SaaS product data (simulated)",
        "nature": "synthetic",
        "banner": "SIMULATED DATA - results describe the simulation, not real users. "
                  "Experiment results are simulations with known ground truth.",
    },
    "ga4": {
        "label": "GA4 Google Merchandise Store sample (observational ecommerce)",
        "nature": "observational",
        "banner": "REAL, OBFUSCATED OBSERVATIONAL DATA - ecommerce web events from 2020-11-01 to "
                  "2021-01-31. Contains placeholder values; cannot establish causal effects.",
    },
}


def resolve_sources(value: str | None = None) -> list[str]:
    """Return the list of sources to process for a configured source type."""
    value = (value or os.environ.get("PULSEIQ_SOURCE", "ga4")).strip().lower()
    if value == "all":
        return list(VALID_SOURCES)
    if value not in VALID_SOURCES:
        raise ValueError(f"PULSEIQ_SOURCE must be one of synthetic, ga4, all; got {value!r}")
    return [value]


def dataset_banner(source: str) -> dict[str, str]:
    """Label the app must display for the active dataset."""
    return SOURCE_LABELS[source]
