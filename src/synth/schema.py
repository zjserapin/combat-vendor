"""DDL for the synthetic Cairn Bank, plus table/column comments.

Comments matter: they are how the agent (and a new analyst) learns that money is
in cents or that flag_reason names the rule that fired. Snowflake supports the
same COMMENT syntax, so the habit carries over.
"""

from datetime import date

from config import Config

TABLES: dict[str, str] = {
    "customers": """
        customer_id INTEGER, full_name VARCHAR, segment VARCHAR, occupation VARCHAR,
        home_city VARCHAR, home_country VARCHAR, onboarded_date DATE, risk_rating INTEGER,
        expected_monthly_cash_cents BIGINT, kyc_refreshed_date DATE""",
    "accounts": """
        account_id INTEGER, customer_id INTEGER, account_type VARCHAR, opened_date DATE,
        balance_cents BIGINT, status VARCHAR""",
    "transactions": """
        account_id INTEGER, txn_ts TIMESTAMP, amount_cents BIGINT, direction VARCHAR,
        channel VARCHAR, counterparty VARCHAR, counterparty_country VARCHAR, txn_city VARCHAR,
        txn_country VARCHAR, status VARCHAR, flag_reason VARCHAR, txn_id BIGINT""",
    "kyc_notes": "customer_id INTEGER, note_date DATE, author VARCHAR, note_text VARCHAR, "
                 "note_id INTEGER",
    "sar_reports": "customer_id INTEGER, typology VARCHAR, status VARCHAR, filed_date DATE, "
                   "narrative VARCHAR, sar_id INTEGER",
    "jurisdiction_risk": "country VARCHAR, risk_tier VARCHAR, note VARCHAR",
}

LABEL_TABLES: dict[str, str] = {
    "alert_dispositions": """
        alert_id VARCHAR, customer_id INTEGER, typology VARCHAR, window_start DATE, window_end DATE,
        human_decision VARCHAR, analyst_id VARCHAR, decided_at TIMESTAMP,
        handle_minutes INTEGER, human_rationale VARCHAR""",
}

# The alert contract. The queue reads only this view, so porting means pointing
# this one view at the source system's alert table — no Python changes.
ALERTS_VIEW = """
CREATE OR REPLACE VIEW {bank}.alerts AS
WITH flagged AS (
    SELECT a.customer_id, t.flag_reason AS typology, t.txn_ts, t.amount_cents, t.status
    FROM {bank}.transactions t
    JOIN {bank}.accounts a ON a.account_id = t.account_id
    WHERE t.status IN ('FLAGGED', 'BLOCKED')
      AND t.flag_reason IS NOT NULL
),
grouped AS (
    SELECT customer_id,
           typology,
           CAST(MIN(txn_ts) AS DATE)                           AS window_start,
           CAST(MAX(txn_ts) AS DATE)                           AS window_end,
           COUNT(*)                                            AS flagged_txns,
           SUM(amount_cents)                                   AS flagged_cents,
           SUM(CASE WHEN status = 'BLOCKED' THEN 1 ELSE 0 END) AS blocked_txns
    FROM flagged
    GROUP BY customer_id, typology
)
SELECT CAST(customer_id AS VARCHAR) || '-' || typology || '-' || CAST(window_start AS VARCHAR)
           AS alert_id,
       customer_id, typology, window_start, window_end, flagged_txns, flagged_cents, blocked_txns
FROM grouped
"""


def make_alert_id(customer_id: int, typology: str, window_start: date) -> str:
    """Python twin of the alert_id expression in ALERTS_VIEW. Keep the two in sync."""
    return f"{customer_id}-{typology}-{window_start.isoformat()}"


TABLE_COMMENTS: dict[str, str] = {
    "customers": "One row per customer. expected_monthly_cash_cents is the cash activity "
                 "declared at onboarding (KYC expected activity).",
    "accounts": "Deposit accounts. balance_cents is the current balance in USD cents.",
    "transactions": "90 days of transactions. status FLAGGED/BLOCKED plus flag_reason "
                    "means a monitoring rule fired. amount_cents is always positive; "
                    "direction says CREDIT (in) or DEBIT (out).",
    "kyc_notes": "Free-text notes from KYC, branch, and operations staff about a customer.",
    "sar_reports": "Suspicious Activity Reports previously filed on a customer.",
    "jurisdiction_risk": "Bank-assigned risk tier per country of counterparty.",
    "alerts": "One row per alert: flagged/blocked activity grouped by customer and "
              "typology. window_start/window_end bound the flagged activity.",
    "alert_dispositions": "GROUND TRUTH. Human analyst decisions per alert. Never exposed "
                          "to the agent; used only for backtesting.",
}

COLUMN_COMMENTS: dict[tuple[str, str], str] = {
    ("transactions", "amount_cents"): "USD cents. Divide by 100 for dollars.",
    ("transactions", "flag_reason"): "Monitoring rule that fired: STRUCTURING, GEO_VELOCITY, "
                                     "RAPID_CASH_OUT, HIGH_RISK_COUNTRY, LARGE_CASH_DEPOSIT.",
    ("transactions", "channel"): "BRANCH_CASH, ATM, WIRE, ACH, or CARD.",
    ("customers", "risk_rating"): "1-100, higher is riskier.",
    ("customers", "expected_monthly_cash_cents"): "Declared expected monthly cash, USD cents.",
}


def create_all(conn, config: Config) -> None:
    """Drop and recreate every table. DuckDB connection; local seeding only."""
    groups = [(config.bank_schema, TABLES), (config.labels_schema, LABEL_TABLES)]
    for schema, tables in groups:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for name, columns in tables.items():
            conn.execute(f"CREATE OR REPLACE TABLE {schema}.{name} ({columns})")
            if name in TABLE_COMMENTS:
                comment = _literal(TABLE_COMMENTS[name])
                conn.execute(f"COMMENT ON TABLE {schema}.{name} IS {comment}")
    conn.execute(ALERTS_VIEW.format(bank=config.bank_schema))
    conn.execute(f"COMMENT ON VIEW {config.bank_schema}.alerts IS "
                 f"{_literal(TABLE_COMMENTS['alerts'])}")
    for (table, column), text in COLUMN_COMMENTS.items():
        target = f"{config.bank_schema}.{table}.{column}"
        conn.execute(f"COMMENT ON COLUMN {target} IS {_literal(text)}")


def _literal(text: str) -> str:
    """COMMENT ON takes no bind parameters, so quote the (trusted, static) text by hand."""
    return "'" + text.replace("'", "''") + "'"
