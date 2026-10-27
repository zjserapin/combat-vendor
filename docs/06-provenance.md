# 06 — Provenance and Clean-Room Note

Where the ideas came from, what was and wasn't taken, and how this repo stays clean.

---

## Inspiration

The architecture was inspired by a **public workshop repository** shared at the AI Engineer conference in 2026: Oracle's *Financial Fraud Agent Harness Workshop* in the public `oracle-devrel/oracle-ai-developer-hub` repository on GitHub, licensed under the **Universal Permissive License (UPL) 1.0**.

That workshop built an AML triage agent on Oracle AI Database. This project applies the same *general patterns* to a different stack (Snowflake + Bedrock), with original code and original synthetic data.

## What was taken: ideas only

General, widely-used agent-engineering patterns:

- "Agent = Model + Harness" framing; a framework-free tool-use loop
- Grouping flagged transactions into alerts by customer and typology
- A harness-assembled evidence pack as the model's primary input
- A constrained decision vocabulary with a human-review fallback
- Step and time budgets on the loop
- Recording each decision both as a structured row and as agent memory
- An impact summary with any assumption explicitly labelled

These are design concepts, not expressions of code. (Even the code itself would have been permissively licensed, but none was used.)

## What was not taken

- **No code** — nothing copied, adapted, or imported from the workshop repository
- **No data** — Cairn Bank is generated from scratch by `src/synth/`
- **No prompts** — all prompts are written fresh in `src/prompts.py`
- **No Oracle-specific features** — no in-database ONNX, Oracle Text, MLE, Spatial, DBFS, or Oracle agent memory package
- **No model providers used in the workshop** — no OCI GenAI or Grok; Anthropic API locally, AWS Bedrock at Ally

## What is original here

- **Benign look-alikes and ambiguous cases** for every typology, with findable explanations — so the demo reflects a real false-positive-heavy queue
- **Ground-truth labels with handle time** in a separate, agent-denied schema
- **A backtest and vendor scorecard** as the primary deliverable
- **The Snowflake/Bedrock adapter design** (views, two-role access, config-only swap)
- **Leakage controls** — shuffled IDs, SAR timing filter, dev/holdout split

## Keeping it clean

- `CLAUDE.md` rule #1 forbids opening or copying code from the workshop repo or any other project folder.
- This repo has no dependency on, import from, or file copied from that repo.
- If a future idea comes from another public source, add it to this file with its license.
