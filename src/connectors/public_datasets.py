"""Download the two public experiment datasets to data/raw/<name>/ (git-ignored).

* Cookie Cats mobile-game A/B test (Kaggle: yufengsui/mobile-games-ab-testing).
  Downloaded from the dataset author's public GitHub mirror; if that fails,
  download the CSV from Kaggle manually and place it at data/raw/cookie_cats/cookie_cats.csv.
* Kevin Hillstrom MineThatData e-mail analytics challenge (2008), from minethatdata.com.

Each file is validated (expected columns and row count) and its SHA-256 is
written to data/metadata/public_datasets.json. Raw files are never committed.

Usage:
    python -m src.connectors.public_datasets            # both
    python -m src.connectors.public_datasets --only cookie_cats
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.settings import PATHS

DATASETS = {
    "cookie_cats": {
        "url": "https://raw.githubusercontent.com/yufung/ab-testing-cookie-cats/master/data/cookie_cats.csv",
        "page": "https://www.kaggle.com/datasets/yufengsui/mobile-games-ab-testing",
        "file": "cookie_cats.csv",
        "columns": ["userid", "version", "sum_gamerounds", "retention_1", "retention_7"],
        "expected_rows": 90_189,
        "credit": "Cookie Cats A/B test, Tactile Entertainment via Yufeng Sui (Kaggle) / DataCamp",
    },
    "hillstrom": {
        "url": "http://www.minethatdata.com/Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv",
        "page": "https://blog.minethatdata.com/2008/03/minethatdata-e-mail-analytics-and-data.html",
        "file": "hillstrom.csv",
        "columns": ["recency", "history_segment", "history", "mens", "womens", "zip_code", "newbie",
                    "channel", "segment", "visit", "conversion", "spend"],
        "expected_rows": 64_000,
        "credit": "Kevin Hillstrom, MineThatData E-Mail Analytics and Data Mining Challenge (2008)",
    },
}


def raw_dir(name: str) -> Path:
    return PATHS.data / "raw" / name


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def validate(name: str, path: Path) -> pd.DataFrame:
    spec = DATASETS[name]
    df = pd.read_csv(path)
    missing = [c for c in spec["columns"] if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: missing columns {missing}; got {list(df.columns)}")
    if len(df) != spec["expected_rows"]:
        raise ValueError(f"{name}: expected {spec['expected_rows']:,} rows, got {len(df):,}")
    return df


def load(name: str) -> pd.DataFrame:
    path = raw_dir(name) / DATASETS[name]["file"]
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python -m src.connectors.public_datasets --only {name}")
    return validate(name, path)


def download(name: str, refresh: bool = False) -> dict:
    spec = DATASETS[name]
    out = raw_dir(name) / spec["file"]
    out.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not out.exists():
        print(f"Downloading {name} from {spec['url']}")
        req = urllib.request.Request(spec["url"], headers={"User-Agent": "PulseIQ/1.0"})
        # python.org macOS builds ship without a CA store; use certifi's bundle.
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:  # noqa: S310 - fixed, known URLs
            out.write_bytes(r.read())
    df = validate(name, out)
    return {"name": name, "path": str(out), "rows": len(df), "sha256": _sha256(out), "source_url": spec["url"],
            "dataset_page": spec["page"], "credit": spec["credit"],
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download public experiment datasets")
    ap.add_argument("--only", choices=list(DATASETS))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    meta_path = PATHS.metadata / "public_datasets.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    status = 0
    for name in [args.only] if args.only else DATASETS:
        try:
            meta[name] = download(name, args.refresh)
            print(f"  {name}: {meta[name]['rows']:,} rows, sha256 {meta[name]['sha256'][:12]}...")
        except Exception as exc:  # network or validation failure
            print(f"  {name} failed: {exc}\n  Manual download: {DATASETS[name]['page']} -> "
                  f"{raw_dir(name) / DATASETS[name]['file']}", file=sys.stderr)
            status = 1
    PATHS.metadata.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
