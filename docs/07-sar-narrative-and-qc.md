# 07 — SAR Narrative Drafting + QC (Blocks 11–12)

Status: **building blocks ready, code-complete and tested with scripted model replies. Nothing has called a live model yet.**

---

## The analogy

Think of a newsroom:

- **The fact-checker's file** is the *fact sheet*. Before anyone writes, the facts are pinned down: names, account numbers, amounts, dates, and the counts and subtotals already added up.
- **The reporter** is the *drafter*. It writes the story, but only from the file. It never does the arithmetic and never adds a fact that isn't in the file.
- **The copy desk** is *QC*. It reads the story against the file and sends it back with a numbered list of fixes. It checks two things: does the copy match the facts (data vs narrative), and is it written to house style (typos, tone, no legal conclusions).
- **The editor** is the *analyst*. Nothing runs (gets filed) without a human sign-off.

The kickbacks we're trying to prevent are the copy desk's job. Most of them are mechanical: a misspelled name, a transposed account number, an amount that doesn't match the transactions, a missing date, a typo, a placeholder left in. So most of the copy desk is **plain code, not a model**.

## The pipeline

```
escalated alert
   │
   ▼
[1] fact sheet ── SQL + arithmetic: subject, accounts, every transaction, totals, subtotals
   │
   ▼
[2] drafter ───── model writes 5 sections through a strict tool; harness assembles + adds closing
   │
   ▼
[3] QC rules ──── deterministic: names, IDs, amounts, dates, counts, language, mechanics
   │
   ▼
[4] QC reviewer ─ independent model call: clarity, completeness, objectivity, spelling
   │
   ├─ errors? ──► back to [2] with a numbered list of findings (at most SAR_MAX_REVISIONS times)
   ▼
[5] ledger ────── every attempt + every finding logged (append-only) → analyst reviews and decides
```

In DS terms: **ingest** (fact sheet) → **generate** (drafter) → **validate** (rules + reviewer) → **retry with feedback** → **persist**. It's the same shape as triage (evidence → agent → validator → ledger), with a feedback edge added.

## Block 11 — the drafter

| Piece | File | What it does |
|---|---|---|
| Fact sheet | `src/sar/facts.py`, `facts_render.py`, `queries.py` | Every flagged + blocked transaction in the window (not capped at 25 like the case file), with account. Completed, attempted, and total computed separately. Subtotals by counterparty, day, account, and channel. |
| Prompts | `src/sar/prompts.py` | `sar_prompt_v1`: five W's + how, Bank's voice, no legal conclusions, no internal labels, never invent: gaps go in `drafting_notes`. Versioned separately from the triage prompt. |
| Tool | `src/sar/schemas.py` | `submit_sar_narrative`, strict: `introduction`, `subject`, `activity`, `basis_for_suspicion`, `investigation`, `cited_txn_ids`, `drafting_notes`. |
| One-shot call | `src/sar/structured_call.py` | One request, one tool call, one nudge, then give up. Same exits as the triage loop (refusal / cut-off → no answer). |
| Drafter | `src/sar/drafter.py` | Validates every section (a stub is a problem, not a pass), assembles in fixed order, appends the fixed closing paragraph. |

**Why sections instead of free text?** Completeness becomes checkable: if `basis_for_suspicion` is empty, that's a fact, not a judgement. The harness owns the structure, the model owns the prose. This is the same split as `submit_decision` in triage.

**Why pre-computed subtotals?** Arithmetic is where language models slip, and a wrong total is a classic kickback. If the harness does all the adding, any amount in the narrative that isn't on the sheet is almost certainly wrong. That lets the amount check be an ERROR, not a soft warning like in triage (D33).

**Why is the triage rationale kept separate?** The drafter sees it as context, but QC never treats it as evidence. If the triage model got an amount wrong, the narrative can't "ground" that mistake by repeating it.

## Block 12 — QC

### Layer 1: rules (`qc_facts.py`, `qc_language.py`). Free, instant, deterministic.

These run on **any** narrative, including one an analyst wrote by hand (`main.py sar-qc`).

| Kickback reason | Check | Severity |
|---|---|---|
| Subject name missing or misspelled | `subject_name`, `name_spelling` (fuzzy match vs. the record) | ERROR |
| Counterparty / place misspelled | `name_spelling` | WARNING |
| Wrong / transposed account or customer number | `unknown_identifier` ("account 501204" not on the case) | ERROR |
| Amount doesn't match the data | `ungrounded_fact` (every $ amount must be in the records) | ERROR |
| Date doesn't match / invalid date | `ungrounded_fact`, `invalid_date` (ISO, MM/DD/YYYY, "September 11, 2026") | ERROR |
| Total or activity dates missing | `total_stated`, `dates_stated` | ERROR |
| Count doesn't match ("five deposits" when there were four) | `transaction_count` | ERROR |
| Cited transaction doesn't exist | `cited_txn` | ERROR |
| Section missing / stub | `draft_structure` | ERROR |
| Placeholder left in (`[TBD]`, `XXX`, `<name>`) | `placeholder` | ERROR |
| Legal conclusion ("laundered", "committed fraud") | `legal_conclusion` | ERROR |
| AI / system reference ("the model", "case file") | `internal_reference` | ERROR |
| Common misspelling ("recieved", "occured") | `misspelling` | ERROR |
| Repeated word ("the the") | `doubled_word` | ERROR |
| Over the length limit | `length` | ERROR |
| Speculation, first person, vague quantities, alert IDs / rule codes, "see attached", amount format, punctuation, capitalization, run-on sentences, mixed date formats, too short | various | WARNING |

### Layer 2: the model reviewer (`qc_review.py`). An independent second read.

- **Maker/checker:** a separate call with its own prompt (`sar_qc_prompt_v1`). It never sees the drafter's notes or reasoning.
- Returns findings (severity, category, exact excerpt, problem, fix), four 1–5 scores (accuracy, completeness, clarity, objectivity), and a PASS/REVISE verdict.
- **Its output is checked too:** if a finding quotes words that aren't in the narrative, it's downgraded to a WARNING. If the review is malformed or missing, the status is `QC_INCOMPLETE`, never a silent pass.
- It covers what rules can't: general spelling and grammar, a chronology that's hard to follow, a "why" that doesn't actually explain the suspicion.

### Statuses (`src/sar/records.py`)

| Status | Meaning |
|---|---|
| `READY_FOR_REVIEW` | No errors from rules or reviewer. An analyst still reviews and decides. |
| `NEEDS_REVISION` | Errors remain after the revision budget. The analyst sees the findings. |
| `QC_INCOMPLETE` | The model reviewer didn't finish. Rules ran, but quality is unknown. |
| `NO_DRAFT` | The drafter produced nothing (refusal, no tool call). An analyst writes it. |

None of these means "file it" (project rule 6).

## Try it (no model calls)

```bash
uv run python main.py sar 10675-GEO_VELOCITY-2026-09-11          # fact sheet + plan
uv run python main.py sar-qc 10675-GEO_VELOCITY-2026-09-11 my_narrative.txt   # rules-only QC
```

With API approval: `sar ALERT_ID --live` drafts → QCs → revises → logs (needs an ESCALATE triage decision, or `--force`); `sar-qc ... --live` adds the model reviewer.

## What the tests prove (51 tests, no network)

- **No false alarms:** a plain, correct narrative generated from each of the 60 alerts' fact sheets passes with **zero errors**.
- **Every planted kickback caught:** 13 planted error types (misspelled name, amount off by $1, wrong date, transposed account, missing total, wrong count, invalid date, placeholder, legal conclusion, doubled word, misspelling, internal reference, AI reference) each produce the expected ERROR. Seven style problems each produce the expected WARNING.
- Fact sheet totals equal the alert contract (`flagged_cents`, `flagged_txns`, `blocked_txns`) for all 60 alerts.
- The revise loop sends the findings back, stops at the budget, and logs every attempt and finding. A drafter refusal gives `NO_DRAFT` with no QC call.
- SAR code never references labels; the pipeline writes only to the agent schema.

## What's not here yet (next blocks)

- **Live run:** draft 5–10 escalated alerts once API calls are approved; read every draft; tune `sar_prompt_v1`.
- **Measure the QC on real kickbacks:** at Ally, take historical QC/QA returns (narrative + reason sent back), run `sar-qc` on the *original* narratives, and measure catch rate by kickback reason. This is the backtest for Block 12, and it needs no drafter at all, so it's the cheapest way to prove value.
- **Grow the rule lists from kickback history.** `MISSPELLINGS` and `PATTERN_RULES` are plain data on purpose.
- **Reviewer UI:** show draft, findings, and fact sheet side by side; capture the analyst's edits (edit distance = a quality metric).
- **Filing-format details:** date format (ISO vs. MM/DD/YYYY), whether attempted amounts count toward the total, continuing-activity reports (prior SAR → "this is a continuing activity report"). These are AML SME decisions, listed in TODO.
- **Timeliness:** filing deadline tracking from the detection date, a workflow check rather than a narrative check.
