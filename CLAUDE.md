# CLAUDE.md — combat-vendor

Inherits global rules from `../CLAUDE.md` (Projects root). This file adds project rules; where they conflict, this file wins for this project.

## Project Context

An in-house **AI investigator for AML alert triage**, built as a proof of concept to show that a small, homegrown agent on data we already own can match or beat third-party vendor tools, faster and with full transparency.

- Runs locally on **Cairn Bank**, a fully synthetic bank in DuckDB. (Unrelated to the `Projects/Cairn` compliance project; it just shares the name.)
- Designed to be lifted to Ally: **DuckDB → Snowflake** and **Anthropic API → Bedrock**, both swapped in `config.py` only.
- Leadership concept: `docs/00-concept-brief.md`. Build status: `docs/02-build-plan.md` (source of truth).

## Institution PoC?

[x] Yes — follows the Ally PoC standards in the global CLAUDE.md.

## Hard Rules (non-negotiable)

1. **Clean room.** The design was inspired by a public conference workshop (see `docs/06-provenance.md`). Do not open, copy, import, or paraphrase code from `Projects/oracle-ai-developer-hub/` or any other project folder. Everything here is written from scratch.
2. **Approved model endpoints only.** Anthropic API (local) or AWS Bedrock (Ally). No OCI GenAI, Grok, OpenAI, or other providers — not even as a fallback.
3. **No Ally anything in this repo.** No Ally data, table names, schema names, vendor names, internal URLs, or credentials. Ally-specific values live only in a `.env` on an Ally machine.
4. **The agent never sees labels.** `labels.*` (human decisions) exists only for backtesting. Agent tools must refuse it — this is target leakage.
5. **The agent never writes to bank data.** Read-only on `bank.*`; it may write only to its own `agent.*` schema (decision log and memory).
6. **The model recommends, a human decides.** Nothing the agent outputs files a SAR or changes a customer record.
7. **Money is integer cents** everywhere (`*_cents` columns). Convert only at display time.

## Key Files

- `main.py` — thin CLI dispatch (`doctor`, `seed`, `queue`, `evidence`, `agent-sql`, `investigate`, `triage`, `report`, `backtest`, `sar`, `sar-qc`); bodies in `src/commands/`
- `config.py` — every setting and both provider swaps
- `src/llm_client.py` — Claude client (Anthropic or Bedrock Mantle) + usage meter
- `src/db.py` — DuckDB or Snowflake behind one `query()` helper + `transaction()`
- `src/alert_queue.py` — reads the `bank.alerts` view (the alert contract), prioritizes, skips decided
- `src/ledger.py` — the only writer; agent schema only; append-only
- `src/evidence/` — point-in-time case file per alert; all SQL in `queries.py`
- `src/tools/` — 4 strict tools; `sql_guard.py` (parser-based) + rolled-back execution protect the data
- `src/prompts.py` — versioned prompts (bump `PROMPT_VERSION` on any change)
- `src/agent.py` — the investigator loop; every non-clean exit → REVIEW_REQUIRED
- `src/decision.py` — validator + routing policy (`finalize`); `src/grounding.py` — cited facts vs evidence
- `src/triage.py` — runs the queue; `src/report/` — impact board (tag every number measured/policy/ASSUMPTION; no accuracy here)
- `src/backtest/` — scorecard vs labels: hash split, metrics with 95% intervals, vendor column (stand-in locally); the only place accuracy is measured
- `src/sar/` — SAR narrative drafting + QC (Blocks 11–12, `docs/07-sar-narrative-and-qc.md`): fact sheet (harness does all arithmetic), drafter, rule QC (`qc_facts.py`, `qc_language.py`), model reviewer, draft → QC → revise pipeline. Own prompt versions in `src/sar/prompts.py` (bump `SAR_PROMPT_VERSION` / `QC_PROMPT_VERSION` on any change). Drafts only: nothing is filed
- `tests/fakes.py` — scripted model responses (real SDK types) — use these, never live calls, in tests
- `src/synth/` — synthetic Cairn Bank generator (local-only; never ported)
- `docs/` — concept brief, architecture, build plan, concepts, port guide, eval plan, decisions

## Commands

```bash
uv sync                      # install
uv run python main.py seed   # build data/cairn_bank.duckdb (~0.5s)
uv run python main.py doctor # check config + DB (--llm makes a model call: NOT until Zach allows)
uv run python main.py queue  # alert inbox
uv run python main.py evidence [ALERT_ID]  # case file the model will see
uv run python main.py agent-sql "SELECT ..."  # run SQL exactly as the agent would
uv run python main.py investigate [ALERT_ID]   # dry run (--live calls the model: NOT until Zach allows)
uv run python main.py triage                   # plan + cost estimate (--live: NOT until Zach allows)
uv run python main.py report                   # impact board
uv run python main.py backtest                 # scorecard vs labels -> reports/scorecard.md
uv run python main.py sar [ALERT_ID]           # SAR fact sheet + plan (--live drafts: NOT until Zach allows)
uv run python main.py sar-qc ALERT_ID FILE     # QC a narrative, rules only (--live adds the model reviewer)
uv run pytest -q             # tests (no network)
uv run ruff check .
```

## Working Style for This Project

- Build **block by block** per `docs/02-build-plan.md`; update the block's status and the decision log when a block lands.
- Explain each block before building it (Zach is learning agent patterns — slow down on the agent loop and tool design).
- Record any non-obvious design choice in `docs/decisions.md`.
