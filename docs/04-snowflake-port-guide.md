# 04 — Snowflake + Bedrock Port Guide

How to move this POC from Cairn Bank (DuckDB + Anthropic API) to Ally (Snowflake + Bedrock) **without changing logic code**.

> Keep this repo Ally-free. Everything Ally-specific — account names, table names, role names — goes in a `.env` and a SQL file **on the Ally side only**.

---

## The idea: an adapter layer of views

**Analogy:** A travel plug adapter. The appliance (our code) has one plug shape — the logical tables in `01-architecture.md`. Ally's warehouse has its own socket shape. A thin set of **views** is the adapter; nothing else changes.

```
 Ally source tables (whatever they're named)
          │
          ▼
 SANDBOX_DB.AML_POC_BANK   ← views named exactly like our logical model
   customers, accounts, transactions, kyc_notes, sar_reports, jurisdiction_risk
 SANDBOX_DB.AML_POC_LABELS ← view over historical dispositions (backtest only)
 SANDBOX_DB.AML_POC_AGENT  ← real tables the agent writes (decision log, memory)
          │
          ▼
 combat-vendor (BANK_SCHEMA=AML_POC_BANK, LABELS_SCHEMA=AML_POC_LABELS, AGENT_SCHEMA=AML_POC_AGENT)
```

### The most important view: `alerts`

The queue reads alerts from exactly one place — `{BANK_SCHEMA}.alerts`. Locally that view groups flagged transactions (see `ALERTS_VIEW` in `src/synth/schema.py`). At Ally you almost certainly have a real alert table from the monitoring system, so map it directly:

```sql
CREATE OR REPLACE VIEW SANDBOX_DB.AML_POC_BANK.ALERTS AS
WITH src AS (
    SELECT
        CAST(<src_alert_id> AS VARCHAR)      AS alert_id,      -- the system's own id
        <src_customer_id>                    AS customer_id,
        <src_rule_typology_mapped>           AS typology,
        CAST(<src_first_activity_ts> AS DATE) AS window_start,
        CAST(<src_last_activity_ts> AS DATE)  AS window_end,
        <src_txn_count>                      AS flagged_txns,
        ROUND(<src_total_amount> * 100)      AS flagged_cents,
        <src_blocked_count>                  AS blocked_txns
    FROM <SOURCE_DB>.<SOURCE_SCHEMA>.<ALERT_TABLE>
    WHERE <src_alert_status> = '<OPEN_OR_CLOSED_FOR_BACKTEST>'
)
SELECT alert_id, customer_id, typology, window_start, window_end,
       flagged_txns, flagged_cents, blocked_txns
FROM src;
```

The historical dispositions view in the labels schema must expose the **same `alert_id`** — that's how the backtest joins agent decisions to human ones.

### Other adapter views

Example (placeholders, not real names):

```sql
CREATE OR REPLACE VIEW SANDBOX_DB.AML_POC_BANK.TRANSACTIONS
COMMENT = 'Adapter view: source transactions mapped to the investigator logical model.'
AS
WITH src AS (
    SELECT
        <src_txn_id>                       AS txn_id,
        <src_account_id>                   AS account_id,
        <src_posted_ts>                    AS txn_ts,
        ROUND(ABS(<src_amount>) * 100)     AS amount_cents,     -- dollars → integer cents
        IFF(<src_amount> >= 0, 'CREDIT', 'DEBIT') AS direction,
        <src_channel_mapped>               AS channel,           -- map to BRANCH_CASH/ATM/WIRE/ACH/CARD
        <src_counterparty_name>            AS counterparty,
        <src_counterparty_country>         AS counterparty_country,
        <src_city>                         AS txn_city,
        <src_country>                      AS txn_country,
        <src_alert_status_mapped>          AS status,            -- COMPLETED/FLAGGED/BLOCKED
        <src_rule_typology_mapped>         AS flag_reason
    FROM <SOURCE_DB>.<SOURCE_SCHEMA>.<SOURCE_TABLE>
    WHERE <src_posted_ts> >= DATEADD(day, -120, CURRENT_DATE)
)
SELECT txn_id, account_id, txn_ts, amount_cents, direction, channel, counterparty,
       counterparty_country, txn_city, txn_country, status, flag_reason
FROM src;
```

Mapping work you'll actually do:

- **Typology mapping** — Ally's monitoring rule IDs → the typologies you pilot on. Start with 2–3 typologies, not all.
- **Alert status** — where "this transaction was alerted" lives (often a separate alert table you join in).
- **Money** — confirm source units; the views produce integer cents.
- **Comments** — add `COMMENT` on views/columns; the agent's `describe_tables` tool reads them.
- **Evidence queries** — all 11 live in `src/evidence/queries.py`. They only use the logical tables, so if the adapter views are right, they run unchanged. Review that one file once against Snowflake.
- **Balances** — locally `balance_cents` is the current balance. If Ally has daily balance snapshots, consider pointing the `accounts` view at the snapshot as of the evidence cutoff (strictly point-in-time).

## Access: two roles, least privilege

| Role | Grants | Used by |
|---|---|---|
| `AML_POC_READER` → `SNOWFLAKE_AGENT_ROLE` | `USAGE` on DB + `AML_POC_BANK`; `SELECT` on its views; **no grant** on `AML_POC_LABELS` or `AML_POC_AGENT` | Agent tools (`run_sql`, `describe_tables`) |
| `AML_POC_WRITER` → `SNOWFLAKE_ROLE` | Reader grants + `CREATE TABLE`, `SELECT, INSERT, UPDATE` on `AML_POC_AGENT` + `SELECT` on `AML_POC_LABELS` | Queue, evidence pack, ledger, memory recall, backtest |

Set a statement timeout on the reader role (e.g. `ALTER USER ... SET STATEMENT_TIMEOUT_IN_SECONDS = 60` or on the warehouse) so a heavy agent query can't run long.

Row-access and masking policies on the source tables apply through the views automatically — the agent sees exactly what that role would see. That's the "existing controls apply" point in the concept brief.

> Locally the agent shares the harness connection and is protected by the parser-based SQL guard plus always-rolled-back execution. At Ally those layers stay, and `agent_database()` adds a separate connection under `SNOWFLAKE_AGENT_ROLE` — the database-level wall.
>
> The SQL guard parses with DuckDB even when the data is in Snowflake. Common Snowflake syntax (`DATEADD`, `IFF`, `QUALIFY`) parses fine; exotic syntax (`LATERAL FLATTEN`, `:` JSON paths, stage `@` references) is rejected with a readable error — fail closed.

## Bedrock

```dotenv
LLM_PROVIDER=bedrock
BEDROCK_MODEL_ID=anthropic.claude-opus-5-5   # or whichever Claude model Ally has approved/enabled
AWS_REGION=us-east-1                         # the region where model access is granted
AWS_PROFILE=<your-sso-profile>
```

- Client: `anthropic.AnthropicBedrockMantle` — same `messages.create`, same tool use, same `Message` type. No code change.
- Credentials: standard AWS chain (SSO profile, env vars, or instance role). No API key.
- Check with Ally cloud/AI platform team: which Claude models are enabled, in which region, and any VPC endpoint / proxy requirements.
- Server-side refusal fallbacks are not available on Bedrock; our design already routes any refusal to `REVIEW_REQUIRED`.
- **Strict tool schemas:** confirm the Bedrock model accepts `strict: true` on tools. If it rejects them, set `TOOLS_STRICT=false` — the Block 6 validator still enforces the decision format.
- Set `BANK_NAME` for the system prompt. Review `src/prompts.py` with an AML SME before any results circulate — the prompt *is* the desk's policy.
- Agree `DISMISS_MIN_CONFIDENCE` with AML and MRM. It's the dial between savings (more auto-dismissals) and risk (fewer).
- Set `INPUT_USD_PER_MTOK` / `OUTPUT_USD_PER_MTOK` to Ally's Bedrock rate so the cost line is right.
- Handle time: if the case-management system records analyst handle time, expose it as `handle_minutes` in the labels view — the board then uses measured time and needs no assumption. Agree `REVIEW_SAMPLE_RATE` (policy) with AML.
- **Backtest labels view:** expose `alert_id, customer_id, typology, window_start, window_end, human_decision, analyst_id, decided_at, handle_minutes, human_rationale`. Map `human_rationale` from the disposition narrative (or `NULL AS human_rationale`). Map the desk's outcomes onto ESCALATE / KYC_REVIEW / DISMISS and record that mapping for MRM.
- **Vendor column:** create `AML_POC_LABELS.VENDOR_SCORES(alert_id, vendor_score)` for the same alerts — the scorecard switches from the stand-in automatically. Keep it in the labels schema so the agent role can't read it.
- Fix `BACKTEST_SPLIT_SALT` before the first run and don't change it; tune prompts on dev disagreements only.
- Agent tables upgrade themselves (`ensure_agent_schema` adds new columns) — the writer role needs `ALTER` on the agent schema's tables.

## Snowflake connection

```dotenv
DB_BACKEND=snowflake
SNOWFLAKE_ACCOUNT=<account_locator>
SNOWFLAKE_USER=<you@ally>
SNOWFLAKE_ROLE=AML_POC_WRITER
SNOWFLAKE_AGENT_ROLE=AML_POC_READER
SNOWFLAKE_WAREHOUSE=<small_wh>
SNOWFLAKE_DATABASE=SANDBOX_DB
SNOWFLAKE_AUTHENTICATOR=externalbrowser
BANK_SCHEMA=AML_POC_BANK
LABELS_SCHEMA=AML_POC_LABELS
AGENT_SCHEMA=AML_POC_AGENT
```

Install the extras: `uv sync --extra ally`.

## SQL dialect notes (DuckDB ↔ Snowflake)

All SQL in `src/` sticks to the overlap. Where they differ:

| Thing | DuckDB | Snowflake | What we do |
|---|---|---|---|
| Bind params | `?` | `%s` default, `?` with `paramstyle='qmark'` | `db.py` sets qmark |
| Date math | `ts - INTERVAL '30 days'` | same works; `DATEADD` also | Use `INTERVAL '30 days'` |
| Cast shorthand | `x::DATE` | `x::DATE` | Both fine |
| Identifier case | lowercase preserved | UPPERCASE unless quoted | `db.query` lowercases keys |
| `information_schema` values | lowercase | UPPERCASE | Compare with `UPPER()` in catalog queries |
| Upsert | `INSERT OR REPLACE` / `ON CONFLICT` | `MERGE` | Not needed: the ledger is append-only (plain `INSERT`) |
| Transactions | `BEGIN` / `COMMIT` / `ROLLBACK` | same | `Database.transaction()`; one cursor per `Database` |
| `CREATE TABLE IF NOT EXISTS` | yes | yes | Agent tables; never dropped |
| Date → text | `CAST(d AS VARCHAR)` → `YYYY-MM-DD` | same (default `DATE_OUTPUT_FORMAT`) | Used in the local `alert_id`; at Ally you use the real alert id |
| Month bucket | `DATE_TRUNC('month', ts)` | same | Wrapped in `CAST(... AS DATE)` for a consistent type |
| Timestamp binds | Python `datetime` | same (TIMESTAMP_NTZ) | All activity bounds are `[start, end)` datetimes |
| `QUALIFY` | supported | supported | OK to use |
| Comments | `COMMENT ON` | `COMMENT ON` / `COMMENT =` | Both fine |
| `CREATE OR REPLACE TABLE` | yes | yes | Agent schema only |

## Port checklist

The step-by-step checklist lives in **[TODO.md](TODO.md) → "Port to Ally"**. It is kept in one place so it can't drift from this guide.

## What does *not* go to Ally

- `src/synth/` and the `seed` command — Ally has real data
- `data/` — local DuckDB file
- Any synthetic numbers in leadership material presented as if they were Ally results
