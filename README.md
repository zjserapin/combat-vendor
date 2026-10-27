# combat-vendor

**An in-house AI investigator for AML alert triage** — proof of concept on *Cairn Bank*, a fully synthetic bank.

It reads the monitoring alert queue, assembles a case file for each alert, lets Claude investigate with read-only tools under a strict budget, and records a decision (`ESCALATE` / `KYC_REVIEW` / `DISMISS`) with a confidence and a written rationale — then measures itself against human analyst decisions.

The point: show that a small homegrown harness on data we already own can match vendor AI tools in weeks, with full transparency. Start with the [concept brief](docs/00-concept-brief.md).

---

## Status

| Block | | |
|---|---|---|
| 0 Foundation (config, LLM client, DB wrapper) | ✅ | |
| 1 Synthetic Cairn Bank | ✅ | 860 customers · 49K txns · 60 labelled alerts |
| 2 Alert queue + decision log | ✅ | `alerts` view contract · append-only ledger |
| 3 Evidence pack | ✅ | 11 point-in-time sections · ~1K tokens · fingerprinted |
| 4 Tools + guardrails | ✅ | 4 strict tools · parser-based SQL guard · rolled-back execution |
| 5 Investigator loop | ✅ | prompt v1 · bounded loop · every exit → decision or human · tested with scripted responses |
| 6 Validator + decision record | ✅ | vocabulary enforced · dismissal threshold · grounding warnings |
| 7 Triage run + impact board | ✅ | **M1 code-complete** — live run pending API go-ahead |
| 8 Backtest + vendor scorecard | ✅ | **M2 code-complete** — dev/holdout split · intervals · vendor column · SME disagreement list; live backtest pending API go-ahead |
| 9–10 Port to Ally, pilot hardening | ⏳ | M3–M4 |
| 11 SAR narrative drafter | ✅ | fact sheet (SQL + arithmetic) · 5-section strict tool · bounded revise loop; live drafts pending API go-ahead |
| 12 SAR QC | ✅ | rules (names, IDs, amounts, dates, counts, typos, legal conclusions) + independent model reviewer; works on any narrative |

Details: [docs/02-build-plan.md](docs/02-build-plan.md)

## Quickstart (< 10 minutes)

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
cd combat-vendor
uv sync                              # install dependencies
uv run python main.py seed           # build data/cairn_bank.duckdb (~1 second)
uv run python main.py doctor         # check config + database
uv run python main.py queue --limit 10   # the alert inbox, most urgent first
uv run python main.py evidence           # the case file the model will see (top alert)
uv run python main.py agent-sql "SELECT * FROM labels.alert_dispositions"   # watch the guard refuse
uv run python main.py investigate        # dry run: exact prompt + case file the model gets
uv run python main.py triage           # plan + cost estimate (add --live to run: makes API calls)
uv run python main.py report           # impact board for the latest run
uv run python main.py backtest         # scorecard vs. analyst labels -> reports/scorecard.md
uv run python main.py sar 10675-GEO_VELOCITY-2026-09-11   # SAR fact sheet + plan (add --live to draft)
uv run python main.py sar-qc ALERT_ID narrative.txt        # QC any narrative: rules only, no model
uv run pytest -q                     # 254 tests, no network
```

To check model access (one tiny call), have `ANTHROPIC_KEY` exported in your shell (or `ANTHROPIC_API_KEY` in `.env`), then:

```bash
uv run python main.py doctor --llm
```

No `.env` is required locally — every setting has a working default. See `.env.example` for the full list.

## Local vs. Ally — the only two switches

| Setting | Local | Ally |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `bedrock` |
| `DB_BACKEND` | `duckdb` | `snowflake` |

Logic code never changes. Install Ally extras with `uv sync --extra ally` and follow the [port guide](docs/04-snowflake-port-guide.md).

## Layout

```
combat-vendor/
├── CLAUDE.md               project rules (clean room, no Ally data, labels off-limits)
├── main.py                 CLI: doctor, seed, queue, evidence, agent-sql, investigate, triage, report,
│                           backtest, sar, sar-qc
├── config.py               every setting + both swaps
├── src/
│   ├── llm_client.py       Claude via Anthropic API or Bedrock Mantle, with usage meter
│   ├── db.py               DuckDB or Snowflake behind one query() helper + transactions
│   ├── alert_queue.py      alerts in the lookback, prioritized, minus already-decided
│   ├── ledger.py           agent schema: decision log, case memory, run log (append-only)
│   ├── evidence/           queries.py (all SQL) · pack.py (build) · render.py (text)
│   ├── tools/              schemas.py · sql_guard.py · handlers.py · toolbox.py
│   ├── prompts.py          prompt_v1 — the desk's policy, versioned
│   ├── agent.py            the investigator loop
│   ├── decision.py         validator: final disposition always in the vocabulary
│   ├── grounding.py        cited amounts/dates checked against the evidence
│   ├── triage.py           work the queue: failure isolation, early stop, run log
│   ├── report/             impact board: metrics.py · render.py (every number tagged)
│   ├── backtest/           scorecard vs. labels (the only place accuracy is measured)
│   ├── sar/                SAR drafting + QC: facts · drafter · qc_facts · qc_language ·
│   │                       qc_review · qc · pipeline (see docs/07)
│   ├── commands/           CLI command bodies
│   └── synth/              synthetic Cairn Bank (local only)
│       ├── models.py  reference.py  context.py  background.py  schema.py  seed.py
│       └── typologies/     structuring, geo_velocity, rapid_cash_out,
│                           high_risk_country, large_cash_deposit
├── tests/                  foundation + seed tests
└── docs/                   see below
```

## Docs

| Doc | For |
|---|---|
| [00 Concept brief](docs/00-concept-brief.md) | Leadership: the case for building the base in-house |
| [01 Architecture](docs/01-architecture.md) | Pipeline, data model, trust boundaries |
| [02 Build plan](docs/02-build-plan.md) | Blocks, exit criteria, status |
| [TODO](docs/TODO.md) | Where we stopped · next live steps · Ally port checklist |
| [03 Concepts explained](docs/03-concepts-explained.md) | How each piece works, analogy → mechanism → code |
| [04 Snowflake + Bedrock port](docs/04-snowflake-port-guide.md) | Moving to Ally without changing logic |
| [05 Evaluation plan](docs/05-evaluation-plan.md) | Backtest, scorecard, vendor comparison |
| [06 Provenance](docs/06-provenance.md) | Clean-room note: where ideas came from |
| [07 SAR drafting + QC](docs/07-sar-narrative-and-qc.md) | Narrative drafter, kickback-prevention QC, what's tested, what's next |
| [Decision log](docs/decisions.md) | Why things are the way they are |

## The synthetic bank in one table

Every typology is planted three ways, so the queue looks like a real one — mostly explainable:

| Typology | True positive → ESCALATE | Ambiguous → KYC_REVIEW | Benign look-alike → DISMISS |
|---|---|---|---|
| Structuring | Sub-$10K cash at several branches, no cash business | Business running ~3× its declared cash, stale KYC | Restaurant depositing Mon/Thu for months, site visit on file |
| Geo-velocity | Home purchase, then foreign spend hours later; last attempt blocked | Foreign spend, no notice, but customer visited before | Travel notice on file; "home" charge is a recurring subscription |
| Rapid cash-out | Third-party wire drained via ATM/branch cash in 48h | Wire from an individual abroad, half withdrawn in cash | Escrow proceeds, pre-announced cash vehicle purchase |
| High-risk country | Unrelated inflows forwarded to a high-risk jurisdiction | First wire to a new high-risk supplier, invoice pending | Monthly wire to long-standing supplier, contract + EDD on file |
| Large cash deposit | New account, low-income profile, cash then wire out | Retiree, large cash, nothing on file | Private vehicle sale, bill of sale scanned |

Human decisions live in `labels.alert_dispositions` — a schema the agent is never allowed to read.
