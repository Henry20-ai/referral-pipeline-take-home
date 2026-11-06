# Referral Program Pipeline

Data profiling and a pandas pipeline that models a referral program (referrers inviting referees), converts timestamps to local time, joins seven source tables into **one row per referral**, and flags rewards that break the program rules (`is_business_logic_valid`). Packaged in Docker.

## What you get

| Output | Location | Purpose |
|---|---|---|
| Referral report (46 rows) | `output/referral_report.csv` | The requested report |
| Invalid findings | `output/referral_invalid_findings.csv` | Every FALSE row with the reason(s) |
| Pipeline summary | `output/pipeline_summary.txt` | Row counts and data-quality observations |
| Data profile | `profiling/data_profile.csv` / `.xlsx` | Null count, distinct count, min/max, length per column |
| Data dictionary (business users) | `docs/data_dictionary.xlsx` | Plain-English column and rule definitions |

## Project structure

```
.
├── Dockerfile
├── requirements.txt
├── README.md
├── data/                       # put the 7 source CSV files here (not committed to git)
├── src/
│   ├── common.py               # paths, logging, CSV loading
│   ├── profile_tables.py       # data profiling
│   └── referral_pipeline.py    # cleaning, joins, time zones, business rules, report
├── docs/
│   ├── build_data_dictionary.py
│   └── data_dictionary.xlsx
├── output/                     # report and findings (written by the pipeline)
└── profiling/                  # profiling results (written by the profiler)
```

> **Note:** the source CSVs are not stored in this repository. Copy the 7 files into `data/` before running.

## Run with Docker (recommended)

Requires Docker Desktop (or Docker Engine). From the project root:

```bash
# 1. Build the image
docker build -t referral-pipeline .

# 2. Run it. The data folder is mounted read-only; results are written to
#    ./output and ./profiling on YOUR machine, i.e. outside the container.
docker run --rm \
  -v "$(pwd)/data:/app/data:ro" \
  -v "$(pwd)/output:/app/output" \
  -v "$(pwd)/profiling:/app/profiling" \
  referral-pipeline
```

Windows PowerShell: replace `$(pwd)` with `${PWD}` and put the command on one line (or use the backtick `` ` `` for line breaks).

The container runs the profiler first, then the pipeline. When it finishes, open `output/referral_report.csv`.

## Run without Docker

Requires Python 3.10+.

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt

python src/profile_tables.py
python src/referral_pipeline.py
```

Optional environment variables: `DATA_DIR` (default `data`), `OUTPUT_DIR` (default `output`), `PROFILING_DIR` (default `profiling`).

To rebuild the Excel data dictionary: `python docs/build_data_dictionary.py`.

## Credentials

None are needed or used. The pipeline reads local CSV files and writes local files. If you later upload the report to cloud storage, load credentials from environment variables or a secrets manager and never put them in the code or the repository.

## How the pipeline works

1. **Load** - read all seven CSVs (`lead_log.csv` is the `lead_logs` table).
2. **Clean** - trim text, parse UTC timestamps, turn `reward_value` ("10 days") into `num_reward_days`, and remove duplicate rows: `user_logs` (29 rows, 9 users) and `lead_logs` (8 rows, 5 leads) keep the latest entry per id.
3. **Join** - everything is left-joined onto `user_referrals` with `validate=` checks, so a join can never create duplicate referrals. `user_referral_logs` (many rows per referral) is first collapsed to one row per referral. Leads are only joined when `referral_source = 'Lead'`.
4. **Time zones** - UTC to local time. `transaction_at` uses the transaction's zone. `referral_at`, `updated_at` and `reward_granted_at` have no zone of their own, so they use: referrer's home-club zone, then transaction zone, then lead zone, then `Asia/Jakarta`.
5. **Business rules** - `is_business_logic_valid` (see below).
6. **Format** - initcap on descriptive text (club names, IDs and phone numbers untouched), null placeholders, column order, and assertions (46 rows, unique referral ids, zero nulls).

## Business rules

A referral is **valid** when it meets a valid condition and triggers no invalid condition.

- **V1** reward > 0, status `Berhasil`, has a transaction that is `PAID` and `NEW`, after the referral and in the same month, referrer's membership not expired and account not deleted, reward granted.
- **V2** status `Menunggu` or `Tidak Berhasil` and no reward value.
- **I1** reward > 0 but status is not `Berhasil`.
- **I2** reward > 0 but no transaction.
- **I3** no reward, but a `PAID` transaction happened after the referral.
- **I4** status `Berhasil` but reward is null or 0.
- **I5** transaction happened before the referral.

Anything meeting neither V1 nor V2 is also FALSE (reported as "Does not satisfy any valid-reward condition").

## Assumptions and decisions (please review)

- **Nulls vs 46 rows.** The brief says nulls should not exist, but also expects 46 rows (one per referral). Rows are never dropped; after the rules are evaluated, nulls become `Unknown` / `No Transaction` / `0` / `1900-01-01 00:00:00`. A placeholder date is not a real date; it is explained in the data dictionary.
- **Before/after** uses exact UTC instants, not two local clocks (Makassar is 1 hour ahead of Jakarta, which would give wrong answers). **Same month** is judged in the referral's local zone.
- **Membership not expired** is checked against the referral date, not today's date (otherwise every 2024 membership would look expired).
- **Reward granted** means any log entry for that referral has `is_reward_granted = True`; `reward_granted_at` is its latest such timestamp.
- **referral_details_id** is the id of the referral's most recent log entry (0 if none).
- **Timestamps keep millisecond precision.** Some referrals were created within a second of their transaction.
- **Rule precedence.** An invalid condition always wins over a valid one.

## Data quality findings

- `user_referral_logs` has 78 distinct referral ids but **only 7 exist in `user_referrals`**; 39 of 46 referrals have no log entry.
- **No reward is marked as granted** for any of the 46 referrals, so V1 can never be met. All 24 TRUE rows come from V2; the 22 FALSE rows are mostly "paid transaction but no reward" (8), and unmet V1 conditions (5).
- 19 referral transaction ids are not found in `paid_transactions`; 13 referrals have no referrer id; 3 transactions occurred before their referral (by 12 s, 12 s and 0.6 s).
- `user_logs` and `lead_logs` contain repeated rows for the same user or lead.
