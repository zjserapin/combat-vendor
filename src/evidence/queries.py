"""Every SQL statement behind the evidence pack, in one place for dialect review.

Conventions: `{bank}` is the bank schema (filled by .format), `?` are bind
params, timestamps are bounded [start, end). Every activity query is capped by
the pack's point-in-time cutoff. None of these touch the labels schema.
"""

TXN_COLUMNS = """t.txn_id, t.txn_ts, t.direction, t.channel, t.amount_cents, t.counterparty,
       t.counterparty_country, t.txn_city, t.txn_country, t.status, t.flag_reason"""

# The customer's accounts. Every activity query starts here.
_ACCOUNTS_CTE = "acct AS (SELECT account_id FROM {bank}.accounts WHERE customer_id = ?)"

CUSTOMER = """
SELECT customer_id, full_name, segment, occupation, home_city, home_country, onboarded_date,
       risk_rating, expected_monthly_cash_cents, kyc_refreshed_date
FROM {bank}.customers
WHERE customer_id = ?
"""

# params: customer_id, typology, window_start_ts, window_end_excl_ts
FLAGGED = f"""
WITH {_ACCOUNTS_CTE}
SELECT {TXN_COLUMNS}
FROM {{bank}}.transactions t
JOIN acct ON acct.account_id = t.account_id
WHERE t.flag_reason = ?
  AND t.status IN ('FLAGGED', 'BLOCKED')
  AND t.txn_ts >= ?
  AND t.txn_ts < ?
ORDER BY t.txn_ts, t.txn_id
LIMIT {{limit}}
"""

# Everything else around the window (any status), biggest first.
# params: customer_id, band_start_ts, band_end_excl_ts, typology
WINDOW_ACTIVITY = f"""
WITH {_ACCOUNTS_CTE}
SELECT {TXN_COLUMNS}
FROM {{bank}}.transactions t
JOIN acct ON acct.account_id = t.account_id
WHERE t.txn_ts >= ?
  AND t.txn_ts < ?
  AND (t.flag_reason IS NULL OR t.flag_reason <> ?)
ORDER BY t.amount_cents DESC, t.txn_id
LIMIT {{limit}}
"""

# Largest transactions before/after the window band, up to the cutoff.
# params: customer_id, context_start_ts, cutoff_excl_ts, band_start_ts, band_end_excl_ts
CONTEXT_ACTIVITY = f"""
WITH {_ACCOUNTS_CTE}
SELECT {TXN_COLUMNS}
FROM {{bank}}.transactions t
JOIN acct ON acct.account_id = t.account_id
WHERE t.txn_ts >= ?
  AND t.txn_ts < ?
  AND NOT (t.txn_ts >= ? AND t.txn_ts < ?)
ORDER BY t.amount_cents DESC, t.txn_id
LIMIT {{limit}}
"""

# params: customer_id, history_start_ts, cutoff_excl_ts
MONTHLY_PROFILE = f"""
WITH {_ACCOUNTS_CTE},
monthly AS (
    SELECT CAST(DATE_TRUNC('month', t.txn_ts) AS DATE) AS month,
           t.channel, t.direction, t.amount_cents
    FROM {{bank}}.transactions t
    JOIN acct ON acct.account_id = t.account_id
    WHERE t.txn_ts >= ?
      AND t.txn_ts < ?
      AND t.status <> 'BLOCKED'
)
SELECT month, channel, direction, COUNT(*) AS txn_count, SUM(amount_cents) AS total_cents
FROM monthly
GROUP BY month, channel, direction
ORDER BY channel, direction, month
"""

# Has this customer dealt with these counterparties before the alert window?
# params: customer_id, history_start_ts, window_start_ts, *counterparties
COUNTERPARTY_HISTORY = f"""
WITH {_ACCOUNTS_CTE}
SELECT t.counterparty, COUNT(*) AS txn_count, SUM(t.amount_cents) AS total_cents,
       MIN(t.txn_ts) AS first_seen, MAX(t.txn_ts) AS last_seen
FROM {{bank}}.transactions t
JOIN acct ON acct.account_id = t.account_id
WHERE t.txn_ts >= ?
  AND t.txn_ts < ?
  AND t.counterparty IN ({{placeholders}})
GROUP BY t.counterparty
ORDER BY t.counterparty
"""

# Activity in these foreign countries before the window (card spend abroad or wires).
# params: customer_id, home_country, history_start_ts, window_start_ts, *countries
COUNTRY_HISTORY = f"""
WITH {_ACCOUNTS_CTE},
located AS (
    SELECT CASE WHEN t.txn_country <> ? THEN t.txn_country ELSE t.counterparty_country END
               AS country,
           t.txn_ts, t.amount_cents
    FROM {{bank}}.transactions t
    JOIN acct ON acct.account_id = t.account_id
    WHERE t.txn_ts >= ?
      AND t.txn_ts < ?
)
SELECT country, COUNT(*) AS txn_count, SUM(amount_cents) AS total_cents,
       MIN(txn_ts) AS first_seen
FROM located
WHERE country IN ({{placeholders}})
GROUP BY country
ORDER BY country
"""

JURISDICTIONS = """
SELECT country, risk_tier, note
FROM {bank}.jurisdiction_risk
WHERE country IN ({placeholders})
ORDER BY country
"""

ACCOUNTS = """
SELECT account_id, account_type, opened_date, balance_cents, status
FROM {bank}.accounts
WHERE customer_id = ?
ORDER BY account_id
"""

# params: customer_id, flows_start_ts, cutoff_excl_ts
FLOWS = f"""
WITH {_ACCOUNTS_CTE}
SELECT t.direction, COUNT(*) AS txn_count, SUM(t.amount_cents) AS total_cents
FROM {{bank}}.transactions t
JOIN acct ON acct.account_id = t.account_id
WHERE t.txn_ts >= ?
  AND t.txn_ts < ?
  AND t.status <> 'BLOCKED'
GROUP BY t.direction
ORDER BY t.direction
"""

# params: customer_id, cutoff_date
KYC_NOTES = """
SELECT note_date, author, note_text
FROM {bank}.kyc_notes
WHERE customer_id = ?
  AND note_date <= ?
ORDER BY note_date DESC, note_id DESC
LIMIT {limit}
"""

# Only SARs filed BEFORE the window: a later SAR would leak the answer.
# params: customer_id, window_start_date
PRIOR_SARS = """
SELECT filed_date, typology, status, narrative
FROM {bank}.sar_reports
WHERE customer_id = ?
  AND filed_date < ?
ORDER BY filed_date DESC
LIMIT {limit}
"""
