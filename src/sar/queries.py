"""SQL behind the SAR fact sheet. Same conventions as src/evidence/queries.py."""

# Every flagged or blocked transaction in the alert window, with the account it hit.
# Unlike the triage case file this is not capped at 25: a SAR reports all of it.
# params: customer_id, typology, window_start_ts, window_end_excl_ts
SAR_TRANSACTIONS = """
WITH acct AS (
    SELECT account_id, account_type
    FROM {bank}.accounts
    WHERE customer_id = ?
)
SELECT t.txn_id, t.txn_ts, t.direction, t.channel, t.amount_cents, t.counterparty,
       t.counterparty_country, t.txn_city, t.txn_country, t.status, t.account_id,
       acct.account_type
FROM {bank}.transactions t
JOIN acct ON acct.account_id = t.account_id
WHERE t.flag_reason = ?
  AND t.status IN ('FLAGGED', 'BLOCKED')
  AND t.txn_ts >= ?
  AND t.txn_ts < ?
ORDER BY t.txn_ts, t.txn_id
LIMIT {limit}
"""
