# Decision Log

Short records of non-obvious choices: what we chose, why, and what we gave up. Newest at the bottom.

---

### D1 — No agent framework
**Chose:** a hand-written tool-use loop on the plain Anthropic SDK.
**Why:** the loop is short, and the questions a model-risk reviewer asks (what did it see, what could it do, when did it stop) should be answerable by reading plain code. Also keeps the Bedrock swap trivial.
**Gave up:** framework conveniences (built-in tracing, retries-per-tool). Add selectively if needed.

### D2 — DuckDB as the local stand-in for Snowflake
**Chose:** DuckDB file + a `Database` wrapper with qmark params and dict rows.
**Why:** zero setup, fast, SQL close to Snowflake's, real `read_only` mode.
**Gave up:** a few dialect differences to manage (see port guide).

### D3 — Synthetic data with benign look-alikes and ambiguous cases
**Chose:** each typology planted as TRUE_POSITIVE / AMBIGUOUS / BENIGN (4 / 2 / 6).
**Why:** demos with only planted fraud flatter any model. Half-benign forces the agent to actually read evidence and makes the backtest meaningful.
**Gave up:** still far cleaner than reality (real queues are often 90%+ false positives).

### D4 — Labels in a separate schema
**Chose:** `labels.alert_dispositions`, never in `bank.*`.
**Why:** lets access controls (DB role + tool allowlist) deny it cleanly — prevents target leakage.

### D5 — Final decision returned through a `submit_decision` tool *(planned, Block 4)*
**Chose:** a `strict: true` tool the model is instructed to call once, plus a harness validator.
**Why:** current Claude models reject *forced* tool choice, but reliably call a tool when instructed; a tool call yields typed arguments instead of parsed prose. Validator covers anything the schema can't.
**Gave up:** the guarantee a forced call would give — handled by routing "no decision" to `REVIEW_REQUIRED`.

### D6 — Default model: `claude-opus-5-5` at `medium` effort
**Chose:** Opus 5.5, effort medium; both in `.env`.
**Why:** this is compliance work where judgment quality matters more than token cost, and the local run is small (60 alerts). Effort is the cost dial on current models.
**Gave up:** cost. Switch to `claude-sonnet-5-5` (half the price) via `LLM_MODEL` and compare in the backtest — a cost/quality table is itself a useful leadership artifact.

### D7 — Money as integer cents
**Chose:** every amount is `*_cents BIGINT`; convert only when rendering text.
**Why:** no float rounding; matches how core banking systems store money; a column comment tells the agent.

### D8 — Memory is a structured table keyed by customer
**Chose:** `agent.case_memory` looked up by `customer_id`.
**Why:** exact lookup covers most of the value ("what did we decide on this customer before?") with no vector infrastructure.
**Gave up:** fuzzy recall across customers — add Snowflake Cortex Search later if needed.

### D9 — Refusals route to a human, no automatic model fallback
**Chose:** `stop_reason == "refusal"` → `REVIEW_REQUIRED`.
**Why:** server-side fallbacks aren't available on Bedrock; one consistent rule across environments is simpler to explain.

### D10 — Fictional high-risk jurisdictions
**Chose:** "Corvania", "Valtessa", "Port Meridia" as HIGH; all real countries LOW.
**Why:** a demo shouldn't label real countries as risky.

### D11 — Shuffled ID pools
**Chose:** customer and account IDs drawn from shuffled pools; transaction IDs assigned in time order.
**Why:** planted cases created last would otherwise have the highest IDs — a leak an LLM can notice.

### D12 — Seeding is local-only
**Chose:** `seed` refuses to run unless `DB_BACKEND=duckdb`.
**Why:** synthetic data must never be written into an Ally environment.

### D13 — Alerts come from a view: the alert contract
**Chose:** the queue reads `bank.alerts` only; locally the view groups flagged transactions, at Ally it maps the monitoring system's alert table.
**Why:** real banks already generate alerts with their own ids. Making the view the contract turns the port into SQL mapping, not Python changes, and gives labels a natural join key (`alert_id`).
**Gave up:** locally, one alert per (customer, typology) — two separate episodes for the same customer would merge. Fine for synthetic data; real alert tables don't have this problem.

### D14 — Append-only ledger
**Chose:** every decision is a new `decision_log` row; "current" = latest by `decided_at` (`QUALIFY ROW_NUMBER()`).
**Why:** examiners want the full history, including superseded decisions; no `MERGE`/upsert dialect differences.
**Gave up:** slightly more complex reads (always take the latest).

### D15 — Overlapping risk ratings (proxy-leak fix)
**Chose:** risk-rating ranges overlap across true positives and benign look-alikes; a test fails if a single threshold separates escalate from dismiss at ≥ 80%.
**Why:** the first seed let `risk_rating >= 60` match 92% of labels — any model would look brilliant by reading one column. Realistic too: legitimate cash-intensive and high-risk-corridor businesses carry high inherent risk ratings.

### D16 — One cursor per Database
**Chose:** `Database` holds a single cursor for its lifetime; `transaction()` issues `BEGIN`/`COMMIT`/`ROLLBACK` on it.
**Why:** in DuckDB each `cursor()` is a separate connection, so a transaction can't span cursors. Snowflake is fine either way.
**Note for Block 4:** DuckDB refuses to open the same file read-only and read-write in one process, so agent read-only enforcement locally will rely on the SQL guard plus running agent queries inside a transaction that is always rolled back. At Ally, the SELECT-only role does it.

### D17 — Point-in-time evidence cutoff
**Chose:** every evidence query is bounded by `min(as_of, window_end + EVIDENCE_AFTER_DAYS)` (default 3); SARs only if filed before the window.
**Why:** the backtest compares against human decisions made a few days after the window. Anything later (a SAR filed because of this alert, a later note) would leak the answer.
**Gave up:** activity in the days after the cutoff that a live triage might see. Tunable per environment.

### D18 — Novelty and baseline sections
**Chose:** the pack includes a 90-day monthly profile and prior history with the alert's counterparties and countries — with "never seen" shown explicitly.
**Why:** the benign look-alikes are only distinguishable by history (months of restaurant cash, a monthly supplier). Without these sections the right answer isn't in the evidence, and no prompt can fix that.

### D19 — Balances are current, not point-in-time (known limitation)
**Chose:** show `balance_cents` labelled "as of <as_of>".
**Why:** the synthetic bank has no balance history. At Ally, point the `accounts` view at a balance snapshot as of the cutoff if one exists.

### D20 — Evidence fingerprint on every decision
**Chose:** `sha256(pack.text)[:16]` stored as `decision_log.evidence_hash`.
**Why:** proves exactly which evidence a decision was made on, and that re-running the same alert produced the same pack — the reproducibility claim in the concept brief, made checkable.

### D21 — Parser-based SQL guard
**Chose:** parse agent SQL with DuckDB's `json_serialize_sql` and check the syntax tree (one SELECT, bank-schema tables or CTEs, no table functions, no risky functions).
**Why:** keyword/regex blocklists are bypassable (quoting, comments, UNION, CTEs) and also block legitimate queries. The parser sees what the database would see. DuckDB is already a dependency and parses common Snowflake syntax.
**Gave up:** exotic Snowflake syntax is rejected (fail closed). Acceptable for an investigator's lookups.

### D22 — Always-rolled-back agent queries; shared connection locally
**Chose:** every agent query runs in `BEGIN … ROLLBACK`; locally the agent shares the harness connection.
**Why:** DuckDB won't open one file both read-only and read-write in a process. The rollback layer makes the guard's failure non-fatal. At Ally a third layer (SELECT-only role, separate connection via `agent_database`) is added.

### D23 — All tools strict; `purpose` on every SQL call; `key_facts` on every decision
**Chose:** `strict: true` everywhere; `run_sql` requires a one-line `purpose`; `submit_decision` requires 2–6 `key_facts`.
**Why:** strict schemas remove a class of malformed-input bugs. `purpose` turns each lookup into an explainable step in the trace. `key_facts` makes grounding checkable — a reviewer (or a later check) can verify each fact against the evidence pack.

### D24 — Tool errors are results, not exceptions
**Chose:** `Toolbox.run` never raises; failures return `is_error: true` with an actionable message.
**Why:** the model can recover (fix a column name, qualify a table); a crash would turn one bad query into a lost alert. The step budget bounds retries.

### D25 — Memory recall uses the harness connection
**Chose:** `recall_case_memory` reads `agent.case_memory` through the harness connection, not the agent's.
**Why:** keeps the agent's Snowflake role to the bank schema only — it never needs a grant on the agent schema.

### D26 — One nudge, then a human
**Chose:** if the model ends a turn without any tool call, send one reminder to call `submit_decision`; a second miss → `REVIEW_REQUIRED`.
**Why:** current models can't be forced to call a tool; one reminder recovers most misses cheaply; more would hide a real problem.

### D27 — Guidance as plain text in the user turn
**Chose:** the nudge and last-step warning are text blocks after the tool results, not mid-conversation system messages.
**Why:** works identically on every endpoint and model version, including Bedrock; no beta features.

### D28 — Model-API errors are failures, not decisions
**Chose:** `investigate()` lets API errors propagate; the run loop records them as failed alerts to retry.
**Why:** "the model was unreachable" says nothing about the customer. Recording it as REVIEW_REQUIRED would inflate the human queue and blur the metrics.

### D29 — Prompt caching deferred
**Chose:** no `cache_control` yet.
**Why:** system prompt + tools are ~1,300 tokens, near or below the minimum cacheable prefix for current models; multi-step alerts re-send the conversation, so caching may still pay off. Measure in the cost pass after the first live backtest.

### D30 — Scripted fakes built from real SDK types
**Chose:** test the loop by replaying real `anthropic.types.Message` objects; assert API message invariants on every test.
**Why:** exercises the exact code path a live response takes, with no network — and catches protocol mistakes (unanswered tool calls, wrong ordering) before they become 400s on a live run.

### D31 — `TOOLS_STRICT` escape hatch
**Chose:** strict tool schemas on by default, switchable off.
**Why:** unverified whether every Bedrock deployment accepts `strict`; the validator still enforces the decision format without it.

### D32 — Asymmetric confidence threshold: only dismissals
**Chose:** a DISMISS below `DISMISS_MIN_CONFIDENCE` (0.6) is routed to a human; escalations and KYC reviews stand at any confidence.
**Why:** a dismissal removes human attention; an escalation adds it. Policy thresholds belong to the harness (and to AML/MRM), not to the model.

### D33 — Grounding misses are warnings, not rejections
**Chose:** amounts/dates the agent cites that aren't in the evidence are stored in `warnings`.
**Why:** a computed total is legitimate and would be a false rejection; but surfacing every miss next to the decision makes hallucination visible and measurable (warning rate in the scorecard).
**Gave up:** a hard guarantee. Revisit after the first live backtest — if warnings correlate with wrong decisions, promote to routing.

### D34 — Keep the model's view when routing to a human
**Chose:** `proposed_decision`, rationale, and key facts are stored even when the final decision is REVIEW_REQUIRED.
**Why:** the human reviewer starts from the agent's reasoning, and the backtest can measure the model's raw judgment separately from the harness's policy.

### D35 — Validator collects every problem
**Chose:** report all failed checks in one `review_reason`.
**Why:** a reviewer (or a prompt fix) sees the whole picture at once instead of peeling problems one by one.

### D36 — Isolate failures, stop on a streak
**Chose:** a failed alert is counted, skipped, and left in the queue; `TRIAGE_MAX_CONSECUTIVE_FAILURES` (3) in a row stops the run.
**Why:** one odd record shouldn't kill a morning's run; a dead endpoint shouldn't burn through the queue recording failures.

### D37 — Impact board: tagged numbers, no accuracy
**Chose:** every figure tagged [measured] / [policy] / [ASSUMPTION]; accuracy lives only in the backtest; an explicit caveat that hours returned depend on the backtest.
**Why:** keeps the business case honest and auditable, and prevents a "hours saved" number from being read as proof that the dismissals were right.

### D38 — Additive schema upgrades for agent tables
**Chose:** `ensure_agent_schema` compares declared columns with the catalog and `ALTER TABLE ... ADD COLUMN` for any missing ones; never drops or changes existing columns.
**Why:** found by a sample run — tables created by an earlier version broke every insert after new columns were added. The same would happen at Ally. Additive-only keeps history safe.

### D39 — The board reads handle time, never the human decision
**Chose:** the only labels column the report touches is `handle_minutes` (tested by source inspection).
**Why:** keeps the impact board and the backtest cleanly separate; the board can't accidentally become an accuracy claim.

### D40 — Split stratified by typology × analyst decision, ordered by hash
**Chose:** within each (typology, human decision) group, rank alerts by `sha256(salt:alert_id)`; the top `BACKTEST_HOLDOUT_SHARE` go to holdout.
**Why:** reproducible on any backend with no RNG; each slice keeps the same mix (a holdout with no escalations makes recall meaningless). Changing `BACKTEST_SPLIT_SALT` is the only way to re-draw, so a re-draw is visible in config.
**Gave up:** a time-based split (train on older alerts, test on newer). Better for drift; revisit at Ally once there are months of history.

### D41 — Every rate carries its counts and a 95% interval
**Chose:** show `hits/total` and a Wilson interval on every rate; target status uses the point estimate.
**Why:** 30 holdout alerts can't distinguish 90% from 100%. Wilson behaves at 0% and 100%, where the textbook interval collapses. A validator will ask; the answer is already on the page.

### D42 — Vendor thresholds fit on dev, real scores override the stand-in
**Chose:** two cut-offs on the vendor score, chosen exhaustively for best dev agreement, applied unchanged to holdout. `labels.vendor_scores` is used if it exists (checked explicitly — a broken table fails loudly rather than falling back); otherwise a stand-in labelled STAND-IN everywhere.
**Why:** the same rule we hold ourselves to, and the most favourable reading for the vendor — so a win is a fair win. The stand-in exists only so the column's code path is exercised.

### D43 — Disagreements exclude REVIEW_REQUIRED; holdout and dev listed apart
**Chose:** the SME list covers alerts where the agent made a different call; holdout and dev under separate headings, holdout marked "do not use to tune prompts".
**Why:** REVIEW_REQUIRED already goes to a human, so listing it is noise. Reading holdout disagreements while editing the prompt is tuning on the test set by another name.

### D44 — Analyst rationale on the labels
**Chose:** `labels.alert_dispositions.human_rationale` (synthetic: one note per planted scenario, no RNG draws).
**Why:** "both rationales side by side" is what lets an SME adjudicate. At Ally map it from the case-management disposition narrative (or `NULL AS human_rationale` if none exists — the scorecard still works).
