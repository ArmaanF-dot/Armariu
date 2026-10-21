"""Paths and constants shared across the pipeline."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from .env without overriding real environment variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

DB_PATH = Path(os.environ.get("RADAR_DB", ROOT / "data" / "radar.db"))
LENDERS_CSV = ROOT / "data" / "lenders.csv"
SCHEMA_PATH = ROOT / "schema.sql"
QUEUE_PATH = ROOT / "classify" / "queue.jsonl"
RESULTS_DIR = ROOT / "classify" / "results"
OUTPUT_DIR = ROOT / "output"
LOGS_DIR = ROOT / "logs"

REST_BASE = "https://api.company-information.service.gov.uk"
STREAM_URL = "https://stream.companieshouse.gov.uk/charges"
FILING_URL = (
    "https://find-and-update.company-information.service.gov.uk/company/{company}/charges/{charge_id}"
)

# Lender categories an agent or rule may assign.
CATEGORIES = (
    "direct_lender",   # private credit fund / direct lender / debt fund
    "bank",            # high-street, challenger or international bank
    "abl_specialist",  # invoice finance, factoring, asset-based lender
    "asset_finance",   # leasing, hire purchase, equipment/vehicle finance
    "property_lender", # mortgage, buy-to-let, bridging or development lender to property companies
    "security_agent",  # security trustee / agent (real lender may be hidden)
    "public_sector",   # government-backed lender or scheme
    "individual",      # private individual or family vehicle
    "other",           # a real chargee that fits none of the above
    "unknown",         # could not tell
)

# When a charge has several chargees, the highest-priority category is the "primary" one.
CATEGORY_PRIORITY = (
    "direct_lender",
    "security_agent",
    "abl_specialist",
    "asset_finance",
    "property_lender",
    "bank",
    "public_sector",
    "individual",
    "other",
    "unknown",
)

# Agent classifications at or above these confidences are used without asking a person.
# Direct lenders and security agents drive the headline tiers, so they need a high bar; the
# other types never reach the headline, so a reasonable answer is good enough ("low stakes").
AUTO_OK_CONFIDENCE = 0.85
AUTO_OK_CONFIDENCE_DIRECT = 0.90
AUTO_OK_CONFIDENCE_LOW_STAKES = 0.60
LOW_STAKES = ("bank", "abl_specialist", "asset_finance", "property_lender", "public_sector", "individual", "other")

REST_LIMIT = 500   # stay under Companies House's 600 requests / 5 minutes per key
REST_WINDOW = 300.0
