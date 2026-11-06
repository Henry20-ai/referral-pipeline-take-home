"""Builds docs/data_dictionary.xlsx - the data dictionary for business (non-technical) users.

Run from the project root:  python docs/build_data_dictionary.py

Sheets:
    Read Me          what the report is and how to read it
    Report Columns   every column in referral_report.csv explained in plain English
    Validation Rules how is_business_logic_valid is decided
    Special Values   what the placeholder values (Unknown, 1900-01-01 ...) mean
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

OUT = Path(__file__).parent / "data_dictionary.xlsx"
FONT = "Arial"
HEADER_FILL = PatternFill("solid", start_color="1F3864")
BAND_FILL = PatternFill("solid", start_color="F2F5FA")
THIN = Side(style="thin", color="D0D7E5")

# (column, plain-English meaning, data type, allowed values / constraints, source table, example)
COLUMNS = [
    ("referral_details_id", "Reference number of the most recent tracking entry recorded for this referral.",
     "Integer", "Whole number. 0 means no tracking entry exists yet for this referral.", "user_referral_logs", "66"),
    ("referral_id", "Unique code that identifies one referral. The report has exactly one row per referral.",
     "Text", "Never empty. Never repeated.", "user_referrals", "7a03e01e0c17b2322845242a796adb5e"),
    ("referral_source", "How the referral was made: a person signing up with a referral code, a transaction started "
     "by staff (draft), or a lead (a prospect who showed interest before joining).",
     "Text", "User Sign Up, Draft Transaction or Lead.", "user_referrals", "Draft Transaction"),
    ("referral_source_category", "Whether the referral came from an online or offline channel. User Sign Up = Online, "
     "Draft Transaction = Offline, Lead = the channel recorded for that lead.",
     "Text", "Online, Offline, or Unknown (a Lead whose record could not be found).", "user_referrals / lead_logs", "Online"),
    ("referral_at", "Date and time the referral was created, in LOCAL time (the referrer's home-club time zone).",
     "Date and time", "Format YYYY-MM-DD HH:MM:SS. Never empty.", "user_referrals", "2024-05-14 13:17:03"),
    ("referrer_id", "Code of the existing member who made the referral (the person who invites).",
     "Text", "Unknown if the source did not record a referrer.", "user_referrals", "2c71c5d66c7e12a0b3c200ba6ed3b78e"),
    ("referrer_name", "Name of the member who made the referral.",
     "Text", "Unknown if the referrer could not be found. Names are anonymised in the source data.", "user_logs", "Jane Doe"),
    ("referrer_phone_number", "Phone number of the member who made the referral.",
     "Text", "Unknown if the referrer could not be found. Anonymised in the source data.", "user_logs", "0812-0000-0000"),
    ("referrer_homeclub", "The club the referrer belongs to. Shown in capital letters, as club names are written.",
     "Text", "Unknown if the referrer could not be found.", "user_logs", "BENHIL"),
    ("referee_id", "Code of the new person who was invited (the referee). For Lead referrals it links to the lead record.",
     "Text", "Unknown if not recorded.", "user_referrals", "f1327c9d6d4efee6ad69e7e467b605b9"),
    ("referee_name", "Name of the new person who was invited.",
     "Text", "Unknown if not recorded. Anonymised in the source data.", "user_referrals", "John Doe"),
    ("referee_phone", "Phone number of the new person who was invited.",
     "Text", "Anonymised in the source data.", "user_referrals", "0813-0000-0000"),
    ("referral_status", "Outcome of the referral. Berhasil = Successful, Menunggu = Pending (still waiting), "
     "Tidak Berhasil = Not successful.",
     "Text", "Berhasil, Menunggu, Tidak Berhasil.", "user_referral_statuses", "Berhasil"),
    ("num_reward_days", "Number of free membership days promised to the referrer for this referral.",
     "Integer", "0 means no reward was assigned. Possible values in the current data: 0, 10, 15, 20.", "referral_rewards", "10"),
    ("transaction_id", "Code of the payment linked to this referral.",
     "Text", "No Transaction if the referral has no payment linked.", "user_referrals", "1d1eb8a9e864a1cccb2d850398461807"),
    ("transaction_status", "Whether the linked payment was completed.",
     "Text", "Paid, or No Transaction. Also No Transaction when the payment code is not in the payments table.", "paid_transactions", "Paid"),
    ("transaction_at", "Date and time of the payment, in LOCAL time at the club where it happened.",
     "Date and time", "Format YYYY-MM-DD HH:MM:SS. 1900-01-01 00:00:00 means there is no transaction.", "paid_transactions", "2024-05-14 13:17:03"),
    ("transaction_location", "The club where the payment was made. Shown in capital letters, as club names are written.",
     "Text", "No Transaction if there is no payment.", "paid_transactions", "ADITYAWARMAN"),
    ("transaction_type", "Kind of payment: New (a new membership) or Rejoin (a returning member).",
     "Text", "New, Rejoin, or No Transaction.", "paid_transactions", "New"),
    ("updated_at", "Date and time the referral record was last changed, in LOCAL time (referrer's time zone).",
     "Date and time", "Format YYYY-MM-DD HH:MM:SS. Never empty.", "user_referrals", "2024-05-14 13:17:34"),
    ("reward_granted_at", "Date and time the reward was given to the referrer, in LOCAL time.",
     "Date and time", "1900-01-01 00:00:00 means the reward has not been granted.", "user_referral_logs", "2024-06-30 14:00:00"),
    ("is_business_logic_valid", "TRUE if the referral and its reward follow the program rules; FALSE if something looks "
     "wrong or suspicious and should be reviewed. See the 'Validation Rules' sheet.",
     "True / False", "TRUE or FALSE. Never empty.", "Calculated", "TRUE"),
]

# (rule id, type, plain-English rule)
RULES = [
    ("V1", "VALID", "Reward is correctly earned: reward is more than 0 days, the referral is Berhasil, it has a payment, "
                    "the payment is Paid and of type New, it happened AFTER the referral and in the SAME MONTH, the referrer "
                    "was a member (not expired) on the referral date, the referrer's account is not deleted, and the reward "
                    "has been granted."),
    ("V2", "VALID", "Nothing to reward: the referral is Menunggu (pending) or Tidak Berhasil (not successful) and no reward is assigned."),
    ("I1", "INVALID", "A reward is assigned (more than 0 days) but the referral is not Berhasil."),
    ("I2", "INVALID", "A reward is assigned (more than 0 days) but the referral has no payment."),
    ("I3", "INVALID", "No reward is assigned, yet a Paid payment happened after the referral was created (a reward may be missing)."),
    ("I4", "INVALID", "The referral is Berhasil but has no reward (empty or 0 days)."),
    ("I5", "INVALID", "The payment happened BEFORE the referral was created."),
    ("Final", "RESULT", "is_business_logic_valid = TRUE only when V1 or V2 is met AND none of I1-I5 applies. "
                        "A referral that meets no valid condition is also FALSE."),
    ("Note", "INFO", "Dates are compared as exact moments in time (UTC). The 'same month' rule uses the referral's local time zone. "
                     "'Membership not expired' is checked on the referral date, not today's date."),
]

# (value shown in report, where it appears, meaning)
SPECIAL = [
    ("Unknown", "Text columns", "The source data does not contain this information (for example the referrer is not in the user table)."),
    ("No Transaction", "transaction_id, status, location, type", "The referral has no payment, or its payment code is not in the payments table."),
    ("1900-01-01 00:00:00", "Date columns", "No date exists (for example no payment, or the reward was never granted). It is a placeholder, not a real date."),
    ("0", "referral_details_id", "No tracking entry exists for this referral."),
    ("0", "num_reward_days", "No reward is assigned."),
]

READ_ME = [
    "Referral Program Report - Data Dictionary",
    "",
    "What is this report? One row per referral: who referred whom, how, what happened with the payment, "
    "what reward is attached, and whether everything is consistent with the program rules.",
    "How many rows? One per referral (46 in the current data). No referral appears twice.",
    "Time: all dates are shown in LOCAL time. The source systems store UTC; they were converted using the time zone "
    "of the referrer's home club (or of the payment / lead when the referrer is unknown).",
    "Empty values: the report contains no blank cells. Where information is missing, a clear placeholder is used - "
    "see the 'Special Values' sheet.",
    "Names and phone numbers: anonymised (scrambled) in the source data, so they will not look like real names.",
    "Questions about a specific FALSE row? The file referral_invalid_findings.csv lists the reason(s) for every FALSE row.",
]


def style_table(sheet, header_row: int, last_row: int, widths: list) -> None:
    """Apply consistent formatting: Arial, dark header, light borders, banding, wrapping."""
    for col_index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=col_index).column_letter].width = width
    for row in sheet.iter_rows(min_row=header_row, max_row=last_row):
        for cell in row:
            cell.font = Font(name=FONT, size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)
            if cell.row > header_row and (cell.row - header_row) % 2 == 0:
                cell.fill = BAND_FILL
    for cell in sheet[header_row]:
        cell.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)


def add_table(workbook, title, headers, rows, widths):
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
    style_table(sheet, 1, len(rows) + 1, widths)


def main() -> None:
    workbook = Workbook()
    readme = workbook.active
    readme.title = "Read Me"
    for index, line in enumerate(READ_ME, start=1):
        cell = readme.cell(row=index, column=1, value=line)
        cell.font = Font(name=FONT, size=14 if index == 1 else 10, bold=(index == 1))
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    readme.column_dimensions["A"].width = 120

    add_table(workbook, "Report Columns",
              ["Column name", "What it means", "Data type", "Allowed values / notes",
               "Comes from", "Example (illustrative)"],
              COLUMNS, [26, 60, 14, 48, 24, 34])
    add_table(workbook, "Validation Rules", ["Rule", "Type", "Plain-English rule"], RULES, [10, 12, 120])
    add_table(workbook, "Special Values", ["Value in report", "Where it appears", "Meaning"], SPECIAL, [24, 40, 90])

    workbook.save(OUT)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
