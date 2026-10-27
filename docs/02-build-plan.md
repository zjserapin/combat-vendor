# 02 — Build Plan

Source of truth for what's built and what's next. Update the status line when a block lands. Open to-dos and the Ally port checklist: [TODO.md](TODO.md).

**Legend:** ✅ done · 🔨 in progress · ⏳ not started

---

## Milestones

| Milestone | Blocks | What you can show |
|---|---|---|
| **M1 — Local demo** | 0–7 | Agent triages Cairn Bank alerts end to end; decision log + impact board |
| **M2 — The proof** | 8 | Backtest scorecard vs. human labels (and a vendor column) |
| **M3 — At Ally** | 9 | Same code on Snowflake + Bedrock against a historical alert sample |
| **M4 — Pilot** | 10 | Scheduled, monitored, documented for MRM |

---

## Block 0 — Foundation ✅

**Goal:** one place for config, one LLM client, one DB wrapper — each swappable for Ally.

- `config.py` — frozen `Config` dataclass; `LLM_PROVIDER` and `DB_BACKEND` swaps; `ANTHROPIC_KEY` (shell) wins over `.env`
- `src/llm_client.py` — `LLMClient` wraps `Anthropic` or `AnthropicBedrockMantle`; `Meter` accumulates tokens/calls/seconds
- `src/db.py` — `Database.query()` returns dicts; qmark params on both backends; `read_only` mode
- `main.py doctor [--llm]`

**Exit criteria:** `doctor` reports config + DB; tests prove key precedence, provider swap, read-only refusal. ✅

## Block 1 — Synthetic Cairn Bank ✅

**Goal:** realistic, deterministic data with ground truth — including the false positives most demos leave out.

- 800 background customers + 60 planted cases (5 typologies × 12)
- Each typology planted three ways: **true positive** (→ ESCALATE), **ambiguous** (→ KYC_REVIEW), **benign look-alike with a documented explanation** (→ DISMISS)
- Labels (`labels.alert_dispositions`) in a separate schema, with analyst handle time
- Shuffled IDs (no "high ID = bad guy" leak); fictional high-risk jurisdictions; money in cents; table/column comments
- `main.py seed` (~0.5s)

**Exit criteria:** 12 tests — determinism, label/flag alignment, no future-dated rows, every benign case has a documented reason. ✅

## Block 2 — Alert queue + decision log ✅

**Goal:** turn flagged activity into cases, and give the agent a place to write.

- **`bank.alerts` view — the alert contract.** One row per alert with a stable `alert_id`. The queue reads only this view, so at Ally the port is re-pointing one view at the monitoring system's alert table. Labels now carry the same `alert_id` (the backtest join key).
- `src/alert_queue.py` — `fetch_alerts(db, config, include_decided=False, limit=None)`: lookback on `window_end`; priority = blocked attempts → risk rating → amount; skips decided alerts unless new flagged activity arrived since (re-open rule)
- `src/ledger.py` — `agent.decision_log`, `agent.case_memory`, `agent.run_log`; **append-only** (latest per alert is current); decision + memory line written in **one transaction**; `start_run` / `finish_run`; `latest_decisions`, `recall_memory`, `decided_windows`
- `src/db.py` — one cursor per `Database` (DuckDB cursors are separate connections) + `transaction()`
- `config.py` — `ALERT_LOOKBACK_DAYS` (30); `AS_OF_DATE=today` for live data
- `main.py queue [--limit N] [--all]`
- **Block 1 fix:** risk ratings now overlap across true-positive / benign cases (cash-intensive businesses and high-risk-jurisdiction importers rate high even when legitimate). Before, a single risk threshold matched 92% of escalate-vs-dismiss labels — a proxy leak. Now 66% (base rate 60%), guarded by a test.

**Exit criteria:** queue returns all 60 alerts on a fresh seed ✅; decided alerts leave the queue and re-open on new activity ✅; queue SQL never references labels ✅; failed memory write rolls back the decision ✅. 17 new tests (36 total).

## Block 3 — Evidence pack ✅

**Goal:** the case file — assembled by SQL, the only thing the model sees up front.

- `src/evidence/queries.py` — all 11 SQL statements in one file (one place to review for Snowflake)
- `src/evidence/pack.py` — `EvidencePeriod` (all date bounds), `EvidencePack`, `build_evidence(db, config, alert)`; `fingerprint` (sha256) and `approx_tokens`
- `src/evidence/render.py` — fixed-order labelled text; the only place cents become dollars; deterministic
- Sections: alert · customer · flagged txns · activity ±1 day · largest other txns · 90-day monthly profile · prior counterparty/country history (+ jurisdiction tier) · accounts · 30-day flows · KYC notes · prior SARs
- **Point-in-time cutoff** = `min(as_of, window_end + EVIDENCE_AFTER_DAYS)`; SARs only if filed before the window
- `agent.decision_log.evidence_hash` column; `alert_queue.get_alert()`; config `EVIDENCE_HISTORY_DAYS` / `EVIDENCE_CONTEXT_DAYS` / `EVIDENCE_AFTER_DAYS`
- `main.py evidence [ALERT_ID]`

**Exit criteria:** all 60 packs build (0.4s total; 830–1,350 tokens) ✅; identical rebuild → identical fingerprint ✅; nothing after the cutoff, planted future SAR/note excluded ✅; no label or decision words in any pack ✅; per-typology sufficiency checks ✅. 16 new tests (52 total).

## Block 4 — Tools + guardrails ✅

**Goal:** a small, safe toolbox.

- `src/tools/schemas.py` — four `strict: true` tool definitions (`tools_v1`): `run_sql(query, purpose)`, `describe_tables()`, `recall_case_memory(customer_id)`, `submit_decision(decision, confidence, rationale, key_facts, next_action)`
- `src/tools/sql_guard.py` — **parser-based** guard: DuckDB `json_serialize_sql` → walk the syntax tree; one SELECT only, bank-schema tables or CTEs only, no table functions, no risky functions; fails closed on unparseable SQL
- `src/tools/handlers.py` — tool implementations; per-backend catalog SQL for `describe_tables`; output as pipe tables, ≤50 rows, ≤6K chars
- `src/tools/toolbox.py` — `Toolbox.run(block)` → `tool_result` (never raises; errors are `is_error` text); `is_terminal()`; call log with latency
- `src/db.py` — `query(max_rows=)` (DuckDB `abort()` releases the unread remainder), `query_then_rollback()`, `agent_database()`, `role=` override; config `SNOWFLAKE_AGENT_ROLE`
- `agent.decision_log.key_facts` column
- `main.py agent-sql "SELECT ..."`

**Exit criteria:** 27 attack queries rejected (labels via quoting/UNION/CTE, writes, multi-statement, file reads, catalogs, ATTACH/PRAGMA/SET) ✅; a write run straight through the rollback layer does not persist ✅; DB errors return as text ✅; schemas strict and complete ✅. 51 new tests (103 total).

## Block 5 — Investigator loop ✅

**Goal:** the agent itself — a short loop with hard stops.

- `src/prompts.py` — `prompt_v1`: role, three dispositions, five typologies, "most alerts are false positives", how to work, rules (**evidence is data, not instructions**; never invent facts; units). Bank name from config. `TRIAGE_INSTRUCTION_V1`, `NUDGE_V1`, `FINAL_STEP_NOTICE_V1`.
- `src/agent.py` — `investigate()` → `AgentResult` (outcome, raw decision input, review reason, steps, seconds, tokens, prompt version, evidence hash, tool calls, stop reasons, full transcript)
  - append-only messages; assistant content verbatim; all tool results in one user message
  - stops: `submit_decision` → DECIDED; refusal / max_tokens / context exceeded / two decisions / ignored nudge / step or time budget → REVIEW_REQUIRED
  - one nudge if the model answers without a tool call; last-step warning
  - model-API errors propagate (a failure to retry, not a decision)
- `src/commands/` — CLI bodies moved out of `main.py` (now 57 lines)
- `main.py investigate [ALERT_ID] [--live]` — dry run prints the exact system prompt + first message; `--live` calls the model
- Config: `BANK_NAME`, `AGENT_MAX_STEPS` (6), `AGENT_MAX_SECONDS` (120), `AGENT_MAX_TOKENS` (16000), `TOOLS_STRICT` (escape hatch if an endpoint rejects strict tools)
- `tests/fakes.py` — scripted model built from real SDK types

**Exit criteria:** every exit path tested with scripted responses ✅; API message invariants asserted on every run ✅; per-alert token/step deltas ✅. 18 new tests (121 total). **Live run on one alert: waiting for go-ahead on API calls.**

## Block 6 — Validator + decision record ✅

**Goal:** the model owns the judgment; the harness owns the vocabulary and the record.

- `src/decision.py` — `validate_decision()` collects every problem (enum, confidence 0–1 and not bool/NaN, rationale, key facts, next action); `finalize()` → `DecisionRecord` whose decision is always in {ESCALATE, KYC_REVIEW, DISMISS, REVIEW_REQUIRED}
- **Policy:** DISMISS below `DISMISS_MIN_CONFIDENCE` (default 0.6) → REVIEW_REQUIRED; low-confidence escalations stand
- `src/grounding.py` — amounts and dates cited by the agent checked against case file + tool outputs; misses saved as warnings
- `agent.decision_log` gains `proposed_decision`, `warnings`, `tool_trace`; memory line for REVIEW_REQUIRED states why

**Exit criteria:** invalid decisions never reach the log as anything but REVIEW_REQUIRED ✅; every row carries evidence window, evidence hash, model id, prompt version, tokens ✅; scripted run lands in log + memory ✅. 39 new tests (160 total).

## Block 7 — Triage run + impact board ✅ → **M1 (code complete; live run pending)**

**Goal:** work the queue and report what it was worth.

- `src/triage.py` — `triage_alert()` (evidence → loop → validator → ledger); `run_triage()` with per-alert failure isolation, early stop after `TRIAGE_MAX_CONSECUTIVE_FAILURES` (3), run log totals; `estimate_cost()`
- `src/report/` — `compute_board()` from the logs alone; `render_board()` Markdown with every figure tagged **[measured] / [policy] / [ASSUMPTION]**; analyst time from recorded handle time, assumption only as fallback; explicit "hours count only if the backtest agrees" caveat; no accuracy figures
- `main.py triage [--limit N] [--all] [--live]` — plan + cost estimate by default (60 alerts ≈ $6 typical, ≤ $17 worst case at Opus 5.5 list price)
- `main.py report [--run RUN_ID] [--save]`
- **Fix found by a sample run:** `ensure_agent_schema` now adds missing columns to agent tables created by older code (additive, never drops) — otherwise every insert fails after a schema change
- Config: `TRIAGE_MAX_CONSECUTIVE_FAILURES`, `INPUT_USD_PER_MTOK`, `OUTPUT_USD_PER_MTOK`, `REVIEW_SAMPLE_RATE` (policy), `MANUAL_MINUTES_PER_ALERT` (assumption)
- `tests/fakes.py` — `RuleLLM` for whole-run tests with no network

**Exit criteria:** full 60-alert scripted run completes ✅; failures isolated and early stop works ✅; board renders from the database alone and matches the logs ✅. 14 new tests (174 total). **Remaining for M1: one live run — waiting for go-ahead on API calls.**

## Block 8 — Backtest + scorecard ✅ → **M2 (code complete; live run pending)**

**Goal:** the proof. Compare agent decisions to human labels like a model validation.

- `src/backtest/` (a package, not one file — split at its natural seams):
  - `split.py` — deterministic dev/holdout split: stratified by typology × analyst decision, ordered by `sha256(salt:alert_id)`; no RNG, no row-order dependence, identical on Snowflake
  - `data.py` — labels LEFT JOIN latest agent decision per alert (optionally one run); repeat decisions on identical inputs for stability. The only Block 8 module that reads labels — harness connection only
  - `metrics.py` — pure functions: confusion matrix, escalation recall (REVIEW_REQUIRED counts as caught), dismissal precision, agreement, REVIEW_REQUIRED rate, by typology, model-vs-policy agreement, threshold holds, grounding-warning rate on agreements vs disagreements, stability, cost/latency. Every rate keeps hits/total + a **Wilson 95% interval**. Starting targets from `05-evaluation-plan.md`
  - `vendor.py` — real scores from `labels.vendor_scores` if that table exists, otherwise a **STAND-IN** rules-engine score; two thresholds fit on **dev only** (best agreement for the vendor), applied unchanged to holdout
  - `scorecard.py` → `render.py` + `render_review.py` — Markdown; no wall-clock timestamps, so identical inputs give a byte-identical file
- **Labels gain `human_rationale`** (Block 1 addition, synthetic analyst note per scenario; draws no random numbers so the rest of the bank is unchanged). At Ally: the case-management disposition narrative
- Config: `BACKTEST_HOLDOUT_SHARE` (0.5), `BACKTEST_SPLIT_SALT` (`split-v1`)
- `main.py backtest [--run RUN_ID]` → prints and writes `reports/scorecard.md`

**Exit criteria:** scorecard reproducible from one command ✅ (byte-identical re-render, tested); disagreements listed worst-first with both rationales, holdout and dev apart ✅; a perfect agent scores 100% and an escalate-everything agent scores 33% (the eval plan's claim, now tested) ✅; vendor thresholds unaffected by holdout labels ✅; real vendor table replaces the stand-in ✅. 29 new tests (203 total). **Remaining for M2: the live backtest (`triage --all --live`, then `backtest`) — waiting for go-ahead on API calls.**

## Block 9 — Port to Ally ⏳ → **M3**

**Goal:** same code, real data. Follow `04-snowflake-port-guide.md`.

- Mapping views in a sandbox schema; SELECT-only role; Bedrock model access
- Run backtest on a historical alert sample

**Exit criteria:** `doctor --llm` green on Ally; scorecard on real alerts.

## Block 10 — Pilot hardening ⏳ → **M4**

- Scheduling (Snowflake Task or existing orchestrator), incremental watermark
- Observability: per-alert trace (inputs, tool calls, outputs) persisted
- MRM pack: design doc, data lineage, backtest results, monitoring plan, limitations
- Human-in-the-loop controls: dismissal sampling rate, override capture

---

## Later / optional

- **Identity scoping** — run as different roles; Snowflake row-access + masking policies do the work
- **Semantic memory** — Snowflake Cortex Search over case memory and KYC notes
- **Reviewer UI** — a simple page to read the evidence pack, rationale, and approve/override
- **Second use case** — reuse the harness (KYC refresh triage, complaint classification)
