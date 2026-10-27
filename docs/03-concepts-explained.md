# 03 — Concepts Explained

The ideas behind the code, each one **analogy first, then the mechanism, then a code sketch**. Sketches for planned blocks are illustrative; the real code lands in the block named.

---

## 1. Agent = Model + Harness

**Analogy:** A junior investigator (the model) at a desk (the harness). The desk decides which case file lands in front of them, which systems they can log into, which forms they must fill in, and when their shift ends.

**Mechanism:** The model only ever does one thing: read a list of messages and produce the next message, which may include a request to call a tool. *Everything else* is our code:

| Harness job | Where it lives |
|---|---|
| What the model sees | Evidence pack (Block 3) |
| What it may do | Tool allowlist + guardrails (Block 4) |
| When it stops | Step + time budgets (Block 5) |
| What counts as an answer | Validator (Block 6) |
| What gets remembered | Decision log + memory (Block 6) |
| What it cost | Meter (Block 0 ✅) |

**Why it matters for the vendor argument:** these six jobs are where a vendor's value supposedly lives. They are a few hundred lines of plain Python.

---

## 2. An alert is not a transaction

**Analogy:** One $9,800 deposit is a data point. Nine of them in two weeks across four branches is a story. Investigators work stories.

**Mechanism:** Group flagged transactions by `(customer, typology)` inside a lookback window. That group is the unit of work, the unit of decision, and the unit of the backtest.

**Built (Block 2):** the grouping lives in one view, `bank.alerts` — the *alert contract*. The queue never touches raw transactions:

```sql
-- src/synth/schema.py: ALERTS_VIEW (local version)
WITH flagged AS (... FLAGGED/BLOCKED transactions joined to their customer ...),
grouped AS (
    SELECT customer_id, typology,
           CAST(MIN(txn_ts) AS DATE) AS window_start,
           CAST(MAX(txn_ts) AS DATE) AS window_end,
           COUNT(*) AS flagged_txns, SUM(amount_cents) AS flagged_cents,
           SUM(CASE WHEN status = 'BLOCKED' THEN 1 ELSE 0 END) AS blocked_txns
    FROM flagged GROUP BY customer_id, typology
)
SELECT <customer_id>-<typology>-<window_start> AS alert_id, ... FROM grouped
```

```python
# src/alert_queue.py
alerts = fetch_alerts(db, config)            # lookback on window_end, priority order
alerts = fetch_alerts(db, config, limit=5)   # top 5
alerts = fetch_alerts(db, config, include_decided=True)  # reruns / backtests
```

Why a view? At Ally the monitoring system already produces alerts. Re-pointing one view at that table is the whole port for this stage — `alert_queue.py` doesn't change.

**Priority** = blocked attempts (the bank's own controls already fired) → customer risk → money at stake. **Re-open rule:** a decided alert comes back if its `window_end` moves past the decided window (new flagged activity).

DS parallel: this is your feature-engineering grain decision. Get the grain wrong and every downstream metric is noise.

---

## 3. The evidence pack — the harness picks the evidence

**Analogy:** A case file a supervisor hands an investigator. The investigator can ask for one or two more documents, but they don't get to decide what's in the folder.

**Mechanism:** SQL assembles a fixed set of sections for each alert and renders them as compact text. Because the same alert always produces the same pack, decisions are **reproducible** — the property MRM and examiners care about most.

**Built (Block 3) — eleven sections, in a fixed order:**

| # | Section | Why an investigator needs it |
|---|---|---|
| 1 | Alert | Typology, window, totals, blocked attempts |
| 2 | Customer | Segment, occupation, tenure, risk rating, **declared expected cash**, KYC age |
| 3 | Flagged transactions | The activity that fired the rule (arithmetic matches the header) |
| 4 | Activity during the window ±1 day | The home purchase hours before the "impossible" foreign one |
| 5 | Largest other transactions | The inbound wire *before* a cash-out; the wire out *after* a cash deposit |
| 6 | 90-day monthly profile | Is this normal for them? (a restaurant's steady cash vs. a sudden spike) |
| 7 | Prior history with these counterparties / countries | Long-standing supplier vs. first-ever wire; been to this country before? Plus bank risk tier |
| 8 | Accounts | New account? Drained balance? |
| 9 | 30-day money in / out | Pass-through behaviour |
| 10 | KYC and operations notes | Where documented explanations live |
| 11 | Prior SARs | History — only those filed **before** the window |

**Point-in-time rule (DS: no future data in features).** Every query is bounded by `cutoff = min(as_of, window_end + EVIDENCE_AFTER_DAYS)`. Notes dated after the cutoff and SARs filed after the window start never reach the model; a test plants both and checks.

**"Never seen" is evidence.** Section 7 shows counterparties and countries with **zero** prior history explicitly (`never`), instead of silently omitting them.

```python
from src.evidence import build_evidence

pack = build_evidence(db, config, alert)
pack.text          # ~1,000 tokens of labelled tables — what the model reads
pack.fingerprint   # sha256 of the text, stored on the decision row
pack.period.cutoff # the point-in-time limit used
```

`python main.py evidence <alert_id>` prints it — the fastest way to see what the agent will see.

**Sufficiency tests.** For every typology the tests check that the evidence *can* support the human's decision: restaurants show months of cash history, smurfs show none; documented suppliers show prior wires, new ones show `never`; cash-outs show the inbound wire; cloned cards show the home purchase and no travel history. If the evidence can't support the right answer, no prompt will fix it.

## 4. Tools, and how Claude actually calls them

**Analogy:** The investigator can pick up the phone to three specific departments. They can't walk into the vault.

**Mechanism — the tool-use protocol:**

1. We send `tools=[...]`, each with a `name`, `description`, and JSON `input_schema`.
2. If Claude wants a tool, the response has `stop_reason == "tool_use"` and one or more `tool_use` blocks, each with an `id`, `name`, and `input`.
3. We run each tool and send back **one user message** containing a `tool_result` block per call, each carrying the matching `tool_use_id`.
4. Repeat until Claude stops asking.

```python
# What the conversation looks like after one tool call
messages = [
    {"role": "user", "content": evidence_text},
    {"role": "assistant", "content": response.content},     # appended verbatim
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01...", "content": "<rows as text>"},
    ]},
]
```

**Two rules that bite:**

- **Append `response.content` verbatim.** It contains thinking blocks the model needs on the next turn. Never rebuild or edit earlier turns — the history is append-only.
- **All tool results for a turn go in one message.** Splitting them teaches the model to stop calling tools in parallel.

**Built (Block 4) — four tools, small on purpose** (every tool is a surface a reviewer has to understand):

| Tool | Why it exists |
|---|---|
| `run_sql(query, purpose)` | Look past the case file — older history, other accounts. `purpose` makes every lookup explainable |
| `describe_tables()` | Learn the schema from table/column comments instead of guessing |
| `recall_case_memory(customer_id)` | "What did we decide about this customer before?" |
| `submit_decision(...)` | The only way to finish — a typed form, not free text |

```python
toolbox = Toolbox(agent_db, config, memory_db=harness_db)
response = llm.create(messages, tools=toolbox.schemas)         # strict schemas, fixed order
results = [toolbox.run(b) for b in response.content if b.type == "tool_use"]
messages.append({"role": "user", "content": results})          # one message, all results
```

**Errors are information, not crashes.** A rejected query, a typo'd column, an unknown tool — each comes back as a `tool_result` with `is_error: true` and a message the model can act on. A DuckDB binder error even suggests the right column name. The loop keeps going; the budget stops runaway retries.

Try it: `python main.py agent-sql "SELECT ..."` runs any query exactly as the agent would.

---

## 5. The loop and its budgets

**Analogy:** A shift has a fixed number of phone calls and a clock. When either runs out, you write up what you have.

**Mechanism:** Two budgets, because each alone fails:

- **Steps only** → a fast model can burn money in a tight loop.
- **Seconds only** → one slow call can eat the whole shift.

**Built (Block 5)** — `src/agent.py`, `investigate(alert, pack, llm, toolbox, config) -> AgentResult`:

```python
messages = [{"role": "user", "content": triage_instruction(pack.text, max_steps)}]
for step in range(1, max_steps + 1):
    if out_of_time:                     return hand_off("time budget exhausted")
    response = llm.create(messages, system=system_prompt(bank_name), tools=toolbox.schemas)
    if response.stop_reason in BAD:     return hand_off(...)          # refusal / cut off / too long
    messages.append({"role": "assistant", "content": response.content})   # verbatim
    calls = tool_use blocks in response
    if two submit_decisions:            return hand_off("more than one decision")
    if one submit_decision:             return decided(its input)
    if no calls:                        nudge once, then hand_off
    messages.append({"role": "user", "content": [toolbox.run(c) for c in calls]})
return hand_off("step budget exhausted")
```

**Every exit:**

| How the turn ends | Outcome |
|---|---|
| `submit_decision` called once | `DECIDED` (input validated in Block 6) |
| Two `submit_decision` calls in one turn | `REVIEW_REQUIRED` — ambiguous |
| Text only, no tool call | One nudge ("call submit_decision now"); second time → `REVIEW_REQUIRED` |
| `refusal` / `max_tokens` / context window exceeded | `REVIEW_REQUIRED` with the reason |
| Step or time budget runs out | `REVIEW_REQUIRED` |
| Model API error (unreachable, rate-limited after retries) | **Raised**, not decided — the run loop (Block 7) records a *failure* to retry |

Two small touches that matter in practice:

- **Nudge once.** Current Claude models can't be *forced* to call a tool, so a model that answers in prose gets one reminder. Counted against the step budget.
- **Last-step warning.** Before the final turn, a text block after the tool results says "this is your last turn — submit now." Plain text inside the user message works on every endpoint (no newer API features needed for Bedrock).

**Testing without a model.** `tests/fakes.py` builds real SDK `Message` / `ToolUseBlock` objects and replays them through the real loop, toolbox, and database. Every test also checks the API's message rules (each `tool_use` answered in order in the next message; results before text) so a live run won't hit a 400.

**See it:** `python main.py investigate <alert_id>` prints the exact system prompt and first message (dry run). `--live` runs the model — off until you allow API calls.

## 6. The decision is a tool call, and the harness owns the vocabulary

**Analogy:** The investigator must fill in a form with three checkboxes. If they write something in the margin instead, the form goes back to the supervisor.

**Mechanism:** The final answer comes through `submit_decision`, whose schema is marked `strict: true` so its arguments always match the schema. The validator then enforces policy the schema can't express.

```python
SUBMIT_DECISION = {
    "name": "submit_decision",
    "description": "Record your triage decision. Call exactly once, when you are done.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "confidence", "rationale", "next_action"],
        "properties": {
            "decision":    {"type": "string", "enum": ["ESCALATE", "KYC_REVIEW", "DISMISS"]},
            "confidence":  {"type": "number"},
            "rationale":   {"type": "string"},
            "next_action": {"type": "string"},
        },
    },
}
```

Why a tool and not "reply in JSON"? Current Claude models don't allow *forcing* a tool call, but they reliably use one when told to — and a tool call gives us a typed payload instead of parsing prose. The validator still checks it (Bedrock parity, defence in depth).

**The rule:** *the model owns the judgment; the harness owns the enum.* Out-of-vocabulary, missing, or refused → `REVIEW_REQUIRED`.

**Built (Block 6)** — `finalize(result, alert, run_id, config, pack_text) -> DecisionRecord`:

| Situation | Final decision | `review_reason` |
|---|---|---|
| Valid decision | that decision | — |
| Any field invalid (bad enum, confidence outside 0–1, empty rationale/facts/next action) | `REVIEW_REQUIRED` | `validator: ...` listing **every** problem |
| `DISMISS` below `DISMISS_MIN_CONFIDENCE` (0.6) | `REVIEW_REQUIRED` | "below the threshold for dismissals" |
| Low-confidence `ESCALATE` / `KYC_REVIEW` | unchanged | — (a human reviews those anyway) |
| Agent handed off (budget, refusal, …) | `REVIEW_REQUIRED` | the agent's reason |

Two choices worth noticing:

- **Asymmetric threshold.** Only *dismissals* need confidence, because a dismissal is the decision that removes human attention. That's a policy dial for AML and MRM to set, not a model setting.
- **The model's view survives a hand-off.** `proposed_decision` and the rationale are kept even when the final decision is `REVIEW_REQUIRED` — the human reviewer starts from the agent's reasoning, and the backtest can measure what the model *would* have done.

**Grounding warnings (`src/grounding.py`).** Every `$amount` and `YYYY-MM-DD` date in the rationale and key facts must appear in the case file or in the agent's own tool results (raw `_cents` values count). Misses are saved as warnings, not rejections — a computed total is legitimate — but a pattern of warnings is the cheapest early signal of hallucination, and a reviewer sees them next to the decision.

---

## 7. Write it down twice

**Analogy:** A case gets a row in the official register (for the examiner) *and* a sticky note in the investigator's own notebook (for next time).

| Store | Shape | Reader |
|---|---|---|
| `agent.decision_log` | Append-only — one row per decision, latest per alert is current: decision, confidence, rationale, evidence window, model, tokens, timestamps | Auditors, dashboards, the backtest — via SQL |
| `agent.case_memory` | One sentence: "2026-09-18 STRUCTURING on 10442: DISMISS (0.86) — documented restaurant cash pattern" | The agent, next run, via `recall_case_memory` |

Different readers, different shapes. Both rows are written in one transaction (`Database.transaction()`), so the register and the notebook never disagree. Locally, memory is keyed by `customer_id` (exact lookup covers most of the value). Semantic search is a later add-on.

---

## 8. Guardrails and target leakage

**Analogy (DS):** Letting the agent read `labels.alert_dispositions` is exactly like training on the target column. The scorecard would look perfect and mean nothing.

**Layers, so one bug can't break them all:**

1. **Parse, don't pattern-match.** Keyword blocklists are easy to dodge (quoted identifiers, comments, `UNION`, CTEs). The guard has DuckDB parse the query into a syntax tree — without executing it — and walks every node: exactly one SELECT, every table in the bank schema, no table functions, no risky functions. It catches `"labels"."alert_dispositions"`, `UNION SELECT … FROM labels…`, labels hidden in a CTE, and `SELECT * FROM read_csv('/etc/passwd')` (a SELECT that reads files). 27 attack queries in the tests.
2. **Assume the guard fails.** Agent SQL runs in a transaction that is always rolled back. A test runs an `INSERT … RETURNING` straight through this layer (bypassing the guard): the row comes back, then vanishes.
3. **Database role at Ally.** A SELECT-only role that has no grant on labels — enforced by Snowflake, not by our code.

The analogy: a bank vault has a guard at the door, a locked cage inside, and an alarm. You don't skip the cage because the guard is good.

Other leakage traps the design avoids:

- **Shuffled IDs** — planted customers aren't clustered at high IDs.
- **SAR timing** — only SARs filed *before* the alert window are evidence.
- **Prompt tuning** — tune on a dev slice of alerts, report on a held-out slice (Block 8).

---

## 9. Metering and the "one labelled assumption" rule

**Analogy:** A business case where every number has a receipt — except one, and that one has a sticky note saying "estimate".

**Built (Block 7)** — `main.py report` renders the board from the decision log, run log, and recorded handle time. Every line carries a tag:

| Tag | Meaning | Examples |
|---|---|---|
| **[measured]** | Read from the logs | decisions, exposure, tokens, cost (tokens × configured price), model time, handle time |
| **[policy]** | A choice the bank makes | 10% of dismissals still re-reviewed; the dismissal confidence threshold |
| **[ASSUMPTION]** | Only where no measurement exists | `MANUAL_MINUTES_PER_ALERT` — used only if handle time isn't recorded |

**Analyst hours returned** = handle time of alerts the agent dismissed × (1 − re-review sample). The board says plainly that these hours count *only if the backtest shows the dismissals are right* — a wrong dismissal is risk, not savings. And it contains **no accuracy numbers at all**: that's the backtest's job, kept separate so the two can't be confused.

**The run itself (`src/triage.py`):** one alert's failure is recorded and skipped (the alert stays in the queue); three in a row stop the run so a dead endpoint doesn't burn the queue. `main.py triage` without `--live` prints a cost estimate first.

Why it matters: one inflated number discredits the whole deck in front of model risk or finance.

## 10. The backtest is a model validation

**Analogy:** You already know this one — a holdout set, a confusion matrix, and a threshold you agree on before you look.

**Mechanism (Block 8):**

```
                    Agent: ESCALATE   KYC_REVIEW   DISMISS   REVIEW_REQ
Human: ESCALATE           ✔ (want ~all)     ok        ✘✘ costly     ok
Human: KYC_REVIEW            ok             ✔          ✘            ok
Human: DISMISS             cost only        ok          ✔ (the savings)  ok
```

- **Escalation recall** — the safety metric. Missing a real SAR is the expensive error.
- **Dismissal precision** — the savings metric. Every correct auto-dismissal is analyst time back.
- **REVIEW_REQUIRED rate** — honesty metric. High = the agent knows when it doesn't know (fine), but too high = no savings.

Humans aren't perfect labels either: disagreements go to an SME for adjudication, and sometimes the agent turns out to be right.

**Built (Block 8)** — `main.py backtest` → `reports/scorecard.md`. Three ideas worth writing down:

- **The split is a hash, not a coin flip.** Each alert's slice depends only on its id, its (typology, analyst decision) group, and a salt. Same alerts → same split, on DuckDB or Snowflake, forever. You tune on dev; holdout is the number you report.
- **Small n deserves an interval.** "100% recall (10/10)" really means "somewhere between ~72% and 100%" (Wilson 95%). Every rate on the scorecard shows that range — it's the first thing a validator asks.
- **Model vs. policy, separated.** The scorecard shows agreement if the model's own call had stood *and* after the harness's dismissal threshold — so you can see exactly what the threshold costs (extra reviews) and buys (bad dismissals stopped).

The vendor column uses the same rule: thresholds fit on dev, scored on holdout. Locally it's a STAND-IN that only proves the code path works.

---

## Walkthrough of what's built (Blocks 0–1)

### `config.py` — one place for every switch

- `load_config()` reads the environment each call (testable with `monkeypatch`), returns a **frozen** dataclass so nothing mutates settings mid-run.
- `ANTHROPIC_KEY` from your shell wins over `ANTHROPIC_API_KEY` from `.env`.
- `Config.model_id` returns the right id for the active provider — logic files never branch on provider.

### `src/llm_client.py` — the swap is one `if`

- `AnthropicBedrockMantle` exposes the same `messages.create` and returns the same `Message` type as `Anthropic`, so there's no response normalization layer to maintain.
- `create()` sends `output_config={"effort": ...}` — effort is how you trade depth for cost on current models (no temperature, no thinking budget).
- `Meter.record()` runs on every call: this is where the cost line on the impact board comes from.
- **It's a class because it holds state** (the SDK client + the meter) across many calls in a run — a class earning its keep.

### `src/db.py` — one `query()` for both warehouses

- Both backends use `?` placeholders (Snowflake set to `qmark` paramstyle) and return lowercase-keyed dicts (Snowflake returns UPPERCASE column names; lowercasing hides that).
- `read_only=True` is a real guarantee on DuckDB; on Snowflake the role provides it.

### `src/synth/` — a bank designed to be hard

| File | Job |
|---|---|
| `models.py` | One dataclass per table |
| `reference.py` | Made-up names, cities, merchants; fictional high-risk jurisdictions |
| `context.py` | Seeded RNG, shuffled ID pools, date helpers — the source of determinism |
| `background.py` | Payroll, groceries, bills, small-business receipts for everyone |
| `typologies/*.py` | One file per typology; each builds a TRUE_POSITIVE, AMBIGUOUS, or BENIGN case |
| `schema.py` | DDL + table/column comments (the agent will read these to learn the schema) |
| `seed.py` | Orchestrates: generate → assign time-ordered IDs → compute balances → CSV → bulk load |

**The design choice that matters most:** each typology has a benign twin whose explanation is *findable in the data* — a site-visit note, a travel notice, a supplier contract, months of consistent history. An agent that escalates everything scores ~33% agreement here. An agent that reads the evidence can score far higher. That's the test real queues set.
