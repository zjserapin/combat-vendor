# 01 — Architecture

How the pieces fit, what talks to what, and where the trust boundaries are.

---

## The pipeline (DS view)

If you think in `ingest → transform → model → output`, the investigator maps cleanly:

| DS stage | Investigator stage | Module (✅ built · ⏳ planned) |
|---|---|---|
| Ingest | **Alert queue** — read the `alerts` view, prioritize, skip decided | ✅ `src/alert_queue.py` |
| Transform / features | **Evidence pack** — assemble the point-in-time case file in SQL | ✅ `src/evidence/` |
| Model | **Tools + guardrails** ✅ `src/tools/`; **investigator loop** ✅ `src/agent.py`; **prompt v1** ✅ `src/prompts.py` | |
| Post-processing | **Validator** — vocabulary, dismissal threshold, grounding warnings | ✅ `src/decision.py`, `src/grounding.py` |
| Output / serving | **Decision log + memory** ✅ `src/ledger.py`; **triage run** ✅ `src/triage.py`; **impact board** ✅ `src/report/` | |
| Evaluation | **Backtest + scorecard** vs. human labels | ✅ `src/backtest/` |
| Infra | Config swap, LLM client, DB wrapper, CLI commands | ✅ `config.py`, `src/llm_client.py`, `src/db.py`, `src/commands/` |
| Data (local only) | Synthetic Cairn Bank | ✅ `src/synth/` |

## End-to-end flow

```
                         ┌────────────────────────── harness (our code) ──────────────────────────┐
                         │                                                                          │
 bank.transactions ──►  [1] Alert queue ──► [2] Evidence pack ──► [3] Investigator loop ──► [4] Validator ──► [5] agent.decision_log
 (FLAGGED/BLOCKED)       │   one row per       SQL case file       │  ▲                         enum + schema      agent.case_memory
                         │   (customer,        (fixed, the only    │  │ read-only tools          check               │
                         │    typology)        thing model sees)   ▼  │                          │                  ▼
                         │                                       Claude (Bedrock / API)          └─► REVIEW_REQUIRED  [6] Impact board
                         │                                                                                             [7] Backtest vs labels.*
                         └──────────────────────────────────────────────────────────────────────────┘
```

### One alert, step by step

1. **Queue** reads `bank.alerts` (the alert contract — see below), keeps alerts whose latest flagged activity is in the lookback window, drops ones already decided (unless new activity arrived since), and orders by blocked attempts → risk rating → amount.
2. **Evidence pack** runs 11 SQL queries (all in `src/evidence/queries.py`), bounded by a **point-in-time cutoff** (`window_end + 3 days`, capped at as-of): customer, flagged transactions, activity around the window, largest other transactions, 90-day monthly profile, prior history with the alert's counterparties and countries (+ jurisdiction risk), accounts, 30-day flows, KYC notes, SARs filed *before* the window. Rendered to ~1,000 tokens of text with a fingerprint (hash) stored on the decision.
3. **Loop** sends `system prompt + evidence pack + tools` to Claude. Claude may call read-only tools (look up more activity, check jurisdiction risk, recall prior decisions). Each round trip is one step; the loop stops at the step or time budget.
4. Claude ends by calling **`submit_decision`** with `{decision, confidence, rationale, next_action}`.
5. **Validator** checks it (`finalize`): valid → that disposition; invalid, missing, refused, or out of budget → `REVIEW_REQUIRED`; a DISMISS below `DISMISS_MIN_CONFIDENCE` → `REVIEW_REQUIRED`. Cited amounts/dates not found in the evidence are saved as warnings.
6. **Decision log** gets a structured row; **case memory** gets a one-line summary the next run can recall.
7. **Meter** records tokens, calls, and seconds for the impact board.

`run_triage` repeats 1–7 for every alert in the queue: a failure on one alert is counted and skipped (it stays in the queue for next time); three failures in a row stop the run. `report` then builds the impact board from the logs alone.

## Data model

### `bank.*` — what the agent may read (read-only)

| Table | Grain | Key columns |
|---|---|---|
| `customers` | customer | `segment`, `occupation`, `risk_rating`, `expected_monthly_cash_cents`, `kyc_refreshed_date` |
| `accounts` | account | `customer_id`, `account_type`, `opened_date`, `balance_cents` |
| `transactions` | transaction | `account_id`, `txn_ts`, `amount_cents`, `direction`, `channel`, `counterparty(_country)`, `status`, `flag_reason` |
| `kyc_notes` | note | `customer_id`, `note_date`, `note_text` |
| `sar_reports` | SAR | `customer_id`, `typology`, `filed_date`, `narrative` |
| `jurisdiction_risk` | country | `risk_tier` |

### `bank.alerts` — the alert contract (view)

One row per alert: `alert_id`, `customer_id`, `typology`, `window_start`, `window_end`, `flagged_txns`, `flagged_cents`, `blocked_txns`. The queue reads **only this view** for alerts. Locally it groups flagged transactions by customer and typology; at Ally it is re-pointed at the monitoring system's alert table. `alert_id` is also the join key to labels.

### `labels.*` — ground truth (agent **denied**)

| Table | Grain | Purpose |
|---|---|---|
| `alert_dispositions` | `alert_id` | Human decision, analyst rationale, handle time. Backtest only. |
| `vendor_scores` (optional) | `alert_id` | Vendor's score for the same alerts. If absent, the scorecard uses a labelled stand-in. |

### `agent.*` — the agent's own state (agent may write)

| Table | Grain | Purpose |
|---|---|---|
| `decision_log` ✅ | decision (append-only; latest per `alert_id` is current) | Final decision, **proposed decision** (what the model chose), confidence, rationale, key facts, review reason, grounding warnings, tool trace, evidence window, evidence hash, model, prompt version, steps, tokens, seconds, timestamp |
| `case_memory` ✅ | note | One-line decision summaries the agent recalls on later runs |
| `run_log` ✅ | run | Per-run totals for the impact board |

The decision row and its memory line are written in **one transaction** — both or neither.

## Trust boundaries

```
              ┌──────────── can read ────────────┐   ┌─ can write ─┐   ┌── never ──┐
 Agent  ───►  │ bank.*  (via allowlisted tools)  │   │  agent.*    │   │ labels.*  │
              └──────────────────────────────────┘   └─────────────┘   │ any DDL   │
                                                                       │ bank DML  │
                                                                       └───────────┘
```

Enforced in **layers**, so a bug in one is caught by another:

1. **SQL guard (parser-based)** — `src/tools/sql_guard.py` parses agent SQL into a syntax tree with DuckDB (without running it) and accepts only: exactly one SELECT, tables in the bank schema (or the query's own CTEs), no table functions (blocks file reads like `read_csv`), no risky functions.
2. **Rolled-back execution** — every agent query runs inside `BEGIN … ROLLBACK` with a row cap (`Database.query_then_rollback`), so even a write that slipped past the guard could not persist.
3. **Database role (Ally)** — the agent's tools connect under `SNOWFLAKE_AGENT_ROLE`: SELECT on the bank schema only, no grant on labels or agent tables.

Locally, DuckDB can't open the same file read-only and read-write in one process, so the agent shares the harness connection and relies on layers 1–2. `agent_database(config, harness_db)` picks the right connection per environment.

### The toolbox

| Tool | Reads | Notes |
|---|---|---|
| `run_sql(query, purpose)` | bank schema | Guarded, rolled back, ≤50 rows, ≤6K chars; `purpose` logged for reviewers |
| `describe_tables()` | bank catalog + comments | Cached per toolbox |
| `recall_case_memory(customer_id)` | `agent.case_memory` | Via the harness connection |
| `submit_decision(decision, confidence, rationale, key_facts, next_action)` | — | Ends the investigation; validated in Block 6 |

All tools are `strict: true`. Every call is logged (`Toolbox.calls`) with input, output, error flag, and latency — the raw material for the per-alert trace.

## Local vs. Ally deployment

| Concern | Local (this repo) | Ally |
|---|---|---|
| Data | DuckDB file, synthetic Cairn Bank | Snowflake views over real tables, mapped to the same logical model |
| Model | Anthropic API (`claude-opus-5-5`) | Bedrock via `AnthropicBedrockMantle` (`anthropic.claude-opus-5-5` or approved equivalent) |
| Credentials | `ANTHROPIC_KEY` in shell | AWS SSO profile + Snowflake SSO (`externalbrowser`) |
| Read-only | DuckDB `read_only=True` | Snowflake role grants |
| Labels | `labels.alert_dispositions` (synthetic) | Historical disposition table/view |
| Scheduling | Manual CLI | Snowflake Task or existing orchestrator (later) |

Switching is `.env` only. See `04-snowflake-port-guide.md`.

## Why no agent framework

The loop is short (a few dozen lines). A framework would hide exactly the parts a model-risk reviewer will ask about — what the model saw, what it could do, when it stopped. Owning the loop keeps those answers in plain code. See `decisions.md` (D1).
