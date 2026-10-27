# TODO — where we stopped, and what's next

Single source of truth for open work. The *how* lives in the linked docs; this is the *what*, in order.

**Paused 2026-10-27.** Blocks 0–8 are code-complete and tested; SAR building blocks (11–12) added the same day (254 tests total, no network). Nothing has called the model yet: every live step below is waiting for a go-ahead on API spend.

---

## 1. Finish locally (Cairn Bank) — M1 + M2

- [ ] **Live run on one alert:** `uv run python main.py investigate --live`. Read the transcript and check the prompt behaves.
- [ ] **Live triage of all 60:** `uv run python main.py triage --all --live` (≈ $6 typical, ≤ $17 worst case), then `uv run python main.py report`. **→ M1 done**
- [ ] **First live backtest:** `uv run python main.py backtest` → `reports/scorecard.md`. **→ M2 done**
- [ ] Tune `prompt_v1` using **dev** disagreements only; bump `PROMPT_VERSION` on every change and re-run all alerts on one version (the scorecard warns if versions are mixed).
- [ ] Stability: re-run the queue 2 more times (`triage --all --live`) so the scorecard's stability row has data (target ≥ 90%).
- [ ] Prompt caching: measure from the first live run whether `cache_control` pays off (decision D29).
- [ ] Grounding warnings: check whether they cluster on disagreements; if so, route them to a human (D33).
- [ ] Rationale quality: score ~20 random rationales on the 1–5 rubric (grounded, complete, examiner-ready). The rubric is in `05-evaluation-plan.md`.

Cairn Bank numbers are synthetic. They prove the method, not the agent, so keep them out of leadership material as results.

## 2. Port to Ally — M3

How-to for every item: [04 Snowflake + Bedrock port guide](04-snowflake-port-guide.md). Keep this repo Ally-free: Ally names and values go only in `.env` and a SQL file on the Ally side.

**Approvals first**
- [ ] Data classification sign-off for sending transaction text to a Bedrock-hosted model
- [ ] Bedrock model access confirmed (model id + region); check whether it accepts `strict` tools (if not: `TOOLS_STRICT=false`)
- [ ] MRM told the POC exists before any result circulates

**Snowflake setup (sandbox)**
- [ ] Sandbox DB + three schemas: bank (views), labels (views), agent (tables)
- [ ] Two roles: agent reader (SELECT on bank only) and harness writer (+ agent schema incl. `ALTER`, + SELECT on labels)
- [ ] Verify the agent role **cannot** read labels or agent schemas, both via `main.py agent-sql` and directly in a worksheet
- [ ] **`alerts` view** pointed at the monitoring system's alert table, with the real `alert_id`
- [ ] Adapter views for 2–3 pilot typologies, with table/column comments (money in integer cents)

**Backtest inputs (labels schema)**
- [ ] Dispositions view: `alert_id, customer_id, typology, window_start, window_end, human_decision, analyst_id, decided_at, handle_minutes, human_rationale`, **filtered to alerts closed before the backtest date**
- [ ] Map the desk's outcomes onto ESCALATE / KYC_REVIEW / DISMISS, and write the mapping down for MRM
- [ ] `human_rationale` from the disposition narrative (or `NULL AS human_rationale`)
- [ ] `handle_minutes` from case management if captured (otherwise the board uses the labelled assumption)
- [ ] Vendor scores for the same alerts → `VENDOR_SCORES(alert_id, vendor_score)`. The scorecard switches off the stand-in automatically.

**Config (`.env` on the Ally machine)**
- [ ] `LLM_PROVIDER=bedrock`, `BEDROCK_MODEL_ID`, `AWS_REGION` / `AWS_PROFILE`
- [ ] `DB_BACKEND=snowflake` + connection settings, `SNOWFLAKE_ROLE`, `SNOWFLAKE_AGENT_ROLE`, schema names
- [ ] `BANK_NAME`; `AS_OF_DATE` (a fixed date for a reproducible backtest)
- [ ] `INPUT_USD_PER_MTOK` / `OUTPUT_USD_PER_MTOK` at Ally's Bedrock rate
- [ ] Fix `BACKTEST_SPLIT_SALT` before the first run and never change it

**Policy to agree with AML + MRM**
- [ ] `DISMISS_MIN_CONFIDENCE` (savings vs. risk dial)
- [ ] `REVIEW_SAMPLE_RATE` (share of agent dismissals still re-reviewed)
- [ ] Scorecard targets (recall ≥ 95%, dismissal precision ≥ 90%, REVIEW_REQUIRED ≤ 20%, stability ≥ 90%). They currently live in `src/backtest/metrics.py`.
- [ ] An AML SME reviews `src/prompts.py`, because the prompt *is* the desk's policy

**Run it**
- [ ] `uv run python main.py doctor --llm` green
- [ ] `investigate <alert_id>` dry run reviewed, then one `--live` alert end to end
- [ ] Backtest on ~50 historical alerts, then scale to a few hundred
- [ ] SME adjudicates every holdout disagreement; report "agent right after adjudication"
- [ ] Consider a time-based split once there are months of history (D40)

**Does not go to Ally:** `src/synth/`, the `seed` command, `data/`.

## 2b. SAR drafting + QC — M5 (Blocks 11–12)

Design: [07-sar-narrative-and-qc.md](07-sar-narrative-and-qc.md). Building blocks are code-complete (51 tests, no network).

- [ ] **Rules-only QC on real narratives (no model, no approval needed):** `uv run python main.py sar-qc ALERT_ID narrative.txt`. Try it on a few sample narratives and note false alarms.
- [ ] **Live draft on a few escalated alerts** (after the live triage run): `uv run python main.py sar --live`. Read every draft, the findings, and `drafting_notes`.
- [ ] Tune `sar_prompt_v1` / `sar_qc_prompt_v1`, bumping the versions on every change.
- [ ] **AML SME decisions:** narrative date format (ISO vs MM/DD/YYYY); whether attempted (blocked) amounts count in "total amount involved"; continuing-activity wording when a prior SAR exists; whether internal customer/account numbers belong in the narrative; current FinCEN narrative length limit (`SAR_NARRATIVE_MAX_CHARS`).
- [ ] **Backtest QC at Ally:** collect historical QC/QA returns (original narrative + kickback reason), run `sar-qc` on the originals, and report catch rate by reason. Add misses to `MISSPELLINGS` / `PATTERN_RULES` (they're plain data).
- [ ] Reviewer UI: draft + findings + fact sheet side by side; capture analyst edits (edit distance as a quality metric).
- [ ] Kickback dashboard from `agent.sar_qc_findings` (findings by check/category, first-draft vs revised).

## 3. Pilot hardening — M4 (Block 10)

- [ ] Scheduling (Snowflake Task or existing orchestrator) + incremental watermark
- [ ] Per-alert trace persisted (inputs, tool calls, outputs)
- [ ] MRM pack: design doc, data lineage, backtest results, monitoring plan, limitations
- [ ] Human-in-the-loop controls: dismissal sampling, override capture
- [ ] Monthly re-backtest for drift
