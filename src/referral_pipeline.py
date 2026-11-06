"""Referral program pipeline.

Reads the seven source tables, cleans them, converts UTC timestamps to local time,
joins everything into ONE row per referral, applies the business rules that flag
potentially fraudulent / incorrect rewards, and writes the final report.

Pipeline steps (each is its own function so it is easy to read and test):
    1. load_raw_tables      - read every CSV                         (common.py)
    2. clean_tables         - fix types, parse numbers, remove duplicate rows
    3. build_referral_frame - join the tables, convert time zones
    4. apply_business_rules - compute is_business_logic_valid (+ the reasons)
    5. finalise_report      - initcap, null handling, column order
    6. write outputs        - referral_report.csv + referral_invalid_findings.csv

Outputs (in the output folder):
    referral_report.csv              the report requested in the brief (one row per referral)
    referral_invalid_findings.csv    every row flagged as not valid, with the reason(s)
    pipeline_summary.txt             row counts and data-quality observations
"""
import logging

import pandas as pd

from common import OUTPUT_DIR, load_raw_tables, setup_logging

log = logging.getLogger("referral_pipeline")

# --------------------------------------------------------------------------- #
# Configuration / assumptions (all documented in README.md and the data dictionary)
# --------------------------------------------------------------------------- #
DEFAULT_TIMEZONE = "Asia/Jakarta"   # last-resort time zone if no table provides one
STATUS_SUCCESS = "Berhasil"          # successful referral
STATUS_NOT_SUCCESS = ["Menunggu", "Tidak Berhasil"]  # pending / failed

# The brief says "null values should not exist" in the report but also expects one
# row per referral (46 rows). So rows are never dropped; nulls are replaced with
# explicit placeholders AFTER the business rules have been evaluated.
PLACEHOLDER_TEXT = "Unknown"
PLACEHOLDER_NO_TRANSACTION = "No Transaction"
PLACEHOLDER_DATETIME = pd.Timestamp("1900-01-01 00:00:00")

FINAL_COLUMNS = [
    "referral_details_id", "referral_id", "referral_source", "referral_source_category",
    "referral_at", "referrer_id", "referrer_name", "referrer_phone_number",
    "referrer_homeclub", "referee_id", "referee_name", "referee_phone",
    "referral_status", "num_reward_days", "transaction_id", "transaction_status",
    "transaction_at", "transaction_location", "transaction_type", "updated_at",
    "reward_granted_at", "is_business_logic_valid",
]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def as_bool(series: pd.Series) -> pd.Series:
    """Turn a condition (which may contain NA from missing data) into a strict True/False.

    A missing value can never satisfy a rule, so NA becomes False.
    """
    return series.fillna(False).astype(bool)


def initcap(value):
    """Capitalise the first letter of each word and lower-case the rest (like Spark's initcap)."""
    if pd.isna(value):
        return value
    return " ".join(word[:1].upper() + word[1:].lower() for word in str(value).split(" "))


def to_local_time(utc_series: pd.Series, tz_series: pd.Series) -> pd.Series:
    """Convert UTC timestamps to local time, using a (possibly different) time zone per row.

    The result is "naive" local time (no tz suffix), i.e. what the wall clock showed.
    Missing timestamps stay missing.
    """
    converted = [
        pd.NaT if pd.isna(ts) else ts.tz_convert(tz).tz_localize(None)
        for ts, tz in zip(utc_series, tz_series)
    ]
    return pd.Series(pd.to_datetime(converted), index=utc_series.index)


# --------------------------------------------------------------------------- #
# Step 2: cleaning
# --------------------------------------------------------------------------- #
def clean_tables(raw: dict) -> dict:
    """Return cleaned copies of the source tables: types fixed, duplicates removed."""
    t = {name: df.copy() for name, df in raw.items()}

    # Trim stray spaces in all text columns and treat empty strings as missing.
    for df in t.values():
        for col in df.columns:
            if not (pd.api.types.is_bool_dtype(df[col]) or pd.api.types.is_numeric_dtype(df[col])):
                df[col] = df[col].astype("string").str.strip().replace("", pd.NA)

    # --- Timestamps: all source timestamps are UTC. -------------------------
    utc_columns = {
        "user_referrals": ["referral_at", "updated_at"],
        "user_referral_logs": ["created_at"],
        "paid_transactions": ["transaction_at"],
        "lead_logs": ["created_at"],
        "referral_rewards": ["created_at"],
        "user_referral_statuses": ["created_at"],
    }
    for table, columns in utc_columns.items():
        for col in columns:
            t[table][col] = pd.to_datetime(t[table][col], utc=True, format="ISO8601")

    # membership_expired_date is a plain calendar date (no time, no zone).
    t["user_logs"]["membership_expired_date"] = pd.to_datetime(t["user_logs"]["membership_expired_date"])

    # --- Numbers -------------------------------------------------------------
    t["user_referrals"]["referral_reward_id"] = t["user_referrals"]["referral_reward_id"].astype("Int64")
    # reward_value arrives as text such as "10 days"; keep just the number of days.
    t["referral_rewards"]["num_reward_days"] = (
        t["referral_rewards"]["reward_value"].str.extract(r"(\d+)", expand=False).astype("Int64")
    )

    # --- Remove duplicate rows so joins cannot multiply the referrals --------
    # user_logs repeats the same user many times (a change-log with identical values):
    # keep one row per user_id, the latest log entry.
    t["user_logs"] = t["user_logs"].sort_values("id").drop_duplicates("user_id", keep="last")
    # lead_logs repeats a lead as its status progresses: keep the latest entry per lead.
    t["lead_logs"] = t["lead_logs"].sort_values("id").drop_duplicates("lead_id", keep="last")
    t["paid_transactions"] = t["paid_transactions"].drop_duplicates("transaction_id", keep="last")

    return t


def summarise_referral_logs(logs: pd.DataFrame) -> pd.DataFrame:
    """Collapse user_referral_logs to ONE row per referral.

    A referral can have many log entries (one referral in the data has 19). We keep:
      - referral_details_id : id of the most recent log entry
      - is_reward_granted   : True if ANY log entry says the reward was granted
      - reward_granted_at   : when the reward was (last) granted, UTC
    """
    logs = logs.sort_values(["created_at", "id"])
    latest = logs.groupby("user_referral_id").tail(1)[["user_referral_id", "id"]]
    latest = latest.rename(columns={"id": "referral_details_id"})

    granted = logs[logs["is_reward_granted"]].groupby("user_referral_id").agg(
        reward_granted_at=("created_at", "max")
    )
    granted["is_reward_granted"] = True

    summary = latest.merge(granted, on="user_referral_id", how="left")
    summary["is_reward_granted"] = summary["is_reward_granted"].fillna(False).astype(bool)
    return summary


# --------------------------------------------------------------------------- #
# Step 3: join the tables and convert time zones
# --------------------------------------------------------------------------- #
def build_referral_frame(t: dict) -> pd.DataFrame:
    """Join all tables onto user_referrals (one row per referral) and localise timestamps."""
    base = t["user_referrals"]

    statuses = t["user_referral_statuses"][["id", "description"]].rename(
        columns={"id": "user_referral_status_id", "description": "referral_status"})
    rewards = t["referral_rewards"][["id", "num_reward_days"]].rename(
        columns={"id": "referral_reward_id"})
    transactions = t["paid_transactions"][[
        "transaction_id", "transaction_status", "transaction_at",
        "transaction_location", "timezone_transaction", "transaction_type"]]
    users = t["user_logs"][[
        "user_id", "name", "phone_number", "homeclub", "timezone_homeclub",
        "membership_expired_date", "is_deleted"]].rename(columns={
            "user_id": "referrer_id", "name": "referrer_name",
            "phone_number": "referrer_phone_number", "homeclub": "referrer_homeclub",
            "timezone_homeclub": "referrer_timezone",
            "is_deleted": "referrer_is_deleted"})
    leads = t["lead_logs"][["lead_id", "source_category", "timezone_location"]].rename(
        columns={"source_category": "lead_source_category", "timezone_location": "lead_timezone"})
    log_summary = summarise_referral_logs(t["user_referral_logs"]).rename(
        columns={"user_referral_id": "referral_id"})

    # validate="many_to_one" makes pandas raise an error if a join would create
    # duplicate referral rows - this is the "make sure there is no duplicate" check.
    df = (
        base
        .merge(statuses, on="user_referral_status_id", how="left", validate="many_to_one")
        .merge(rewards, on="referral_reward_id", how="left", validate="many_to_one")
        .merge(transactions, on="transaction_id", how="left", validate="many_to_one")
        .merge(users, on="referrer_id", how="left", validate="many_to_one")
        .merge(leads, left_on="referee_id", right_on="lead_id", how="left", validate="many_to_one")
        .merge(log_summary, on="referral_id", how="left", validate="one_to_one")
    )

    # Leads are only relevant when the referral came from a lead. For any other source
    # the referee_id must not be matched to the lead table (see the ERD note).
    is_lead = df["referral_source"].eq("Lead").fillna(False).astype(bool)
    df.loc[~is_lead, ["lead_source_category", "lead_timezone"]] = pd.NA

    # referral_source_category - the CASE expression from the brief.
    df["referral_source_category"] = pd.NA
    df.loc[df["referral_source"].eq("User Sign Up").fillna(False).astype(bool), "referral_source_category"] = "Online"
    df.loc[df["referral_source"].eq("Draft Transaction").fillna(False).astype(bool), "referral_source_category"] = "Offline"
    df.loc[is_lead, "referral_source_category"] = df.loc[is_lead, "lead_source_category"]

    # --- Time zones -----------------------------------------------------------
    # referral_at / updated_at / reward_granted_at have no zone of their own, so we
    # borrow one from a joined table, in this order of preference:
    #   referrer's home-club zone -> transaction zone -> lead zone -> default zone
    df["referral_timezone"] = (
        df["referrer_timezone"].fillna(df["timezone_transaction"])
        .fillna(df["lead_timezone"]).fillna(DEFAULT_TIMEZONE)
    )
    # Keep the original UTC instants: "is the transaction before/after the referral?" is a
    # question about absolute time, so it must NOT be answered by comparing two local clocks
    # that may be in different zones (e.g. Makassar is 1 hour ahead of Jakarta).
    df["referral_at_utc"] = df["referral_at"]
    df["transaction_at_utc"] = df["transaction_at"]
    # The transaction expressed in the referral's own zone, used for the "same month" rule.
    df["transaction_at_in_referral_tz"] = to_local_time(df["transaction_at_utc"], df["referral_timezone"])

    for col in ["referral_at", "updated_at", "reward_granted_at"]:
        df[col] = to_local_time(df[col], df["referral_timezone"])
    # In the report a transaction is shown in its own zone; if missing, use the referral's.
    df["transaction_at"] = to_local_time(
        df["transaction_at"], df["timezone_transaction"].fillna(df["referral_timezone"]))

    return df


# --------------------------------------------------------------------------- #
# Step 4: business rules
# --------------------------------------------------------------------------- #
def apply_business_rules(df: pd.DataFrame) -> pd.DataFrame:
    """Add is_business_logic_valid and a plain-English reason for every row that is not valid.

    "Before/after" comparisons use the original UTC instants; the "same month" rule uses
    local time in the referral's time zone. Membership is checked against the referral date.
    A row is valid when it meets a "valid" condition AND triggers no "invalid" condition.
    """
    status = df["referral_status"]
    reward_positive = as_bool(df["num_reward_days"].fillna(0) > 0)
    has_transaction = df["transaction_id"].notna()
    txn_paid = as_bool(df["transaction_status"].str.upper().eq("PAID"))
    txn_new = as_bool(df["transaction_type"].str.upper().eq("NEW"))
    # Before/after: compare the absolute (UTC) instants.
    txn_after_referral = as_bool(df["transaction_at_utc"] > df["referral_at_utc"])
    txn_before_referral = as_bool(df["transaction_at_utc"] < df["referral_at_utc"])
    # Same month: both timestamps expressed in the referral's local time zone.
    same_month = as_bool(
        (df["transaction_at_in_referral_tz"].dt.year == df["referral_at"].dt.year)
        & (df["transaction_at_in_referral_tz"].dt.month == df["referral_at"].dt.month))
    # "Membership has not expired" is judged on the referral date (not today's date),
    # because the question is whether the referrer was a member WHEN they referred.
    membership_active = as_bool(
        df["membership_expired_date"].dt.normalize() >= df["referral_at"].dt.normalize())
    not_deleted = as_bool(df["referrer_is_deleted"].eq(False))
    reward_granted = as_bool(df["is_reward_granted"].eq(True))
    is_success = as_bool(status.eq(STATUS_SUCCESS))
    is_pending_or_failed = as_bool(status.isin(STATUS_NOT_SUCCESS))

    # ---- Valid conditions --------------------------------------------------
    valid_1 = (reward_positive & is_success & has_transaction & txn_paid & txn_new
               & txn_after_referral & same_month & membership_active & not_deleted & reward_granted)
    valid_2 = is_pending_or_failed & ~reward_positive

    # ---- Invalid conditions (each one has a readable explanation) ----------
    invalid_rules = {
        "Reward value given but referral status is not 'Berhasil'": reward_positive & ~is_success,
        "Reward value given but the referral has no transaction": reward_positive & ~has_transaction,
        "No reward value although a PAID transaction happened after the referral": (
            ~reward_positive & has_transaction & txn_paid & txn_after_referral),
        "Referral is 'Berhasil' but has no reward value (null or 0)": is_success & ~reward_positive,
        "Transaction happened before the referral was created": txn_before_referral,
    }
    any_invalid = pd.Series(False, index=df.index)
    for mask in invalid_rules.values():
        any_invalid = any_invalid | mask

    df["is_business_logic_valid"] = (valid_1 | valid_2) & ~any_invalid

    # Reasons (used for the findings file). Rows that are not valid but match none of
    # the explicit invalid rules simply fail to satisfy any "valid" condition.
    def reasons_for(position: int) -> str:
        found = [text for text, mask in invalid_rules.items() if mask.iloc[position]]
        if not found and not df["is_business_logic_valid"].iloc[position]:
            found = ["Does not satisfy any valid-reward condition"]
        return "; ".join(found)

    df["invalid_reason"] = [reasons_for(i) for i in range(len(df))]
    return df


# --------------------------------------------------------------------------- #
# Step 5: finishing touches
# --------------------------------------------------------------------------- #
def finalise_report(df: pd.DataFrame) -> pd.DataFrame:
    """Apply initcap, replace nulls with placeholders and order the final columns."""
    report = df.copy()

    # Initcap on descriptive text. Club names (homeclub, transaction_location) keep
    # their original upper case, and identifiers (ids, phone numbers) are left untouched.
    for col in ["referral_source", "referral_source_category", "referrer_name", "referee_name",
                "referral_status", "transaction_status", "transaction_type"]:
        report[col] = report[col].map(initcap)

    # Replace nulls so the report contains none (rows are never dropped).
    report["referral_details_id"] = report["referral_details_id"].fillna(0).astype(int)   # 0 = no log entry
    report["num_reward_days"] = report["num_reward_days"].fillna(0).astype(int)
    for col in ["transaction_id", "transaction_status", "transaction_location", "transaction_type"]:
        report[col] = report[col].fillna(PLACEHOLDER_NO_TRANSACTION)
    for col in ["referral_source_category", "referrer_id", "referrer_name", "referrer_phone_number",
                "referrer_homeclub", "referee_id", "referee_name", "referee_phone", "referral_status"]:
        report[col] = report[col].fillna(PLACEHOLDER_TEXT)
    for col in ["referral_at", "transaction_at", "updated_at", "reward_granted_at"]:
        report[col] = report[col].fillna(PLACEHOLDER_DATETIME)

    return report[FINAL_COLUMNS]


def write_summary(raw: dict, t: dict, df: pd.DataFrame, report: pd.DataFrame) -> str:
    """Build a short text summary of row counts and data-quality observations."""
    logs = raw["user_referral_logs"]
    lines = [
        "REFERRAL PIPELINE SUMMARY",
        "=" * 60,
        f"Referrals in source (user_referrals): {len(raw['user_referrals'])}",
        f"Rows in final report:                 {len(report)}",
        f"Valid rewards (is_business_logic_valid = True):  {int(report['is_business_logic_valid'].sum())}",
        f"Invalid / not valid:                              {int((~report['is_business_logic_valid']).sum())}",
        "",
        "DATA QUALITY OBSERVATIONS",
        "-" * 60,
        f"user_logs: {len(raw['user_logs'])} rows but only {raw['user_logs']['user_id'].nunique()} distinct users (duplicates removed)",
        f"lead_logs: {len(raw['lead_logs'])} rows but only {raw['lead_logs']['lead_id'].nunique()} distinct leads (latest status kept)",
        f"user_referral_logs: {logs['user_referral_id'].nunique()} distinct referral ids, but only "
        f"{logs['user_referral_id'].isin(raw['user_referrals']['referral_id']).groupby(logs['user_referral_id']).first().sum()} exist in user_referrals",
        f"Referrals with no log entry: {int((report['referral_details_id'] == 0).sum())}",
        f"Referrals with no transaction id: {int(raw['user_referrals']['transaction_id'].isna().sum())}",
        f"Referral transaction ids not found in paid_transactions: "
        f"{int(raw['user_referrals']['transaction_id'].dropna().isin(raw['paid_transactions']['transaction_id']).eq(False).sum())}",
        f"Referrals with no referrer id: {int(raw['user_referrals']['referrer_id'].isna().sum())}",
        f"Referrals whose reward was granted (per logs): {int(df['is_reward_granted'].fillna(False).sum())}",
        "",
        "INVALID REASONS (a row can have several)",
        "-" * 60,
    ]
    reason_counts = df["invalid_reason"].str.split("; ").explode().replace("", pd.NA).dropna().value_counts()
    lines += [f"{count:3d}  {reason}" for reason, count in reason_counts.items()]
    return "\n".join(lines)


def main() -> None:
    setup_logging()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_raw_tables()                      # 1. load
    cleaned = clean_tables(raw)                  # 2. clean
    frame = build_referral_frame(cleaned)        # 3. join + time zones
    frame = apply_business_rules(frame)          # 4. business rules
    report = finalise_report(frame)              # 5. format

    # Safety checks: one row per referral, and no nulls left in the report.
    assert len(report) == len(raw["user_referrals"]), "Row count changed during joins!"
    assert report["referral_id"].is_unique, "Duplicate referrals in report!"
    assert report.isna().sum().sum() == 0, "Null values remain in the report!"

    report.to_csv(OUTPUT_DIR / "referral_report.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")

    findings = frame.loc[~frame["is_business_logic_valid"], ["referral_id", "referral_source",
                                                              "referral_status", "num_reward_days",
                                                              "transaction_id", "invalid_reason"]]
    findings.to_csv(OUTPUT_DIR / "referral_invalid_findings.csv", index=False)

    summary = write_summary(raw, cleaned, frame, report)
    (OUTPUT_DIR / "pipeline_summary.txt").write_text(summary, encoding="utf-8")
    log.info("Report rows: %d | valid: %d | not valid: %d", len(report),
             int(report["is_business_logic_valid"].sum()),
             int((~report["is_business_logic_valid"]).sum()))
    log.info("Outputs written to %s", OUTPUT_DIR.resolve())


if __name__ == "__main__":
    main()
