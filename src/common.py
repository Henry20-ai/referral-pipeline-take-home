"""Shared helpers for the referral pipeline: configuration, logging and CSV loading.

Both scripts (profile_tables.py and referral_pipeline.py) import from here so that
file locations and table names are defined in exactly one place.

No credentials are used anywhere in this project. Everything is read from local
CSV files and written to a local folder, so nothing secret can end up in the code.
"""
import logging
import os
from pathlib import Path

import pandas as pd

# Folders can be overridden with environment variables (this is how Docker
# points the code at the mounted volumes). Defaults work for a local run.
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
PROFILING_DIR = Path(os.getenv("PROFILING_DIR", "profiling"))

# Logical table name -> accepted CSV file names.
# The lead table is described as `lead_logs` in the brief but ships as `lead_log.csv`,
# so both spellings are accepted.
TABLE_FILES = {
    "lead_logs": ["lead_logs.csv", "lead_log.csv"],
    "user_referrals": ["user_referrals.csv"],
    "user_referral_logs": ["user_referral_logs.csv"],
    "user_logs": ["user_logs.csv"],
    "user_referral_statuses": ["user_referral_statuses.csv"],
    "referral_rewards": ["referral_rewards.csv"],
    "paid_transactions": ["paid_transactions.csv"],
}


def setup_logging() -> logging.Logger:
    """Configure console logging once and return the project logger."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("referral_pipeline")


def load_raw_tables(data_dir: Path = DATA_DIR) -> dict:
    """Load every source CSV into a DataFrame, exactly as delivered (no cleaning).

    Returns a dict keyed by the logical table name, e.g. tables["user_referrals"].
    Raises a clear error if a file is missing instead of failing later with a
    confusing KeyError.
    """
    log = logging.getLogger("referral_pipeline")
    tables = {}
    for table_name, candidates in TABLE_FILES.items():
        path = next(
            (data_dir / name for name in candidates if (data_dir / name).exists()),
            None,
        )
        if path is None:
            raise FileNotFoundError(
                f"Could not find a CSV for table '{table_name}' in '{data_dir}'. "
                f"Expected one of: {candidates}"
            )
        tables[table_name] = pd.read_csv(path)
        log.info("Loaded %-24s %4d rows x %d columns  (%s)",
                 table_name, *tables[table_name].shape, path.name)
    return tables
