# Build the Base: An In-House AI Investigator for AML Alert Triage

*Concept brief for leadership · Draft v0.1 · Owner: Zach Serapin*

---

## The one-sentence version

The model is a commodity we can already reach; the value is in the few hundred lines of harness around it — and that harness can sit on the data we already own, inside the controls we already have, in weeks rather than years.

## The ask

Approve a **time-boxed pilot** of an in-house AI investigator that triages AML monitoring alerts on our own Snowflake data using Claude on Bedrock, and measure it head-to-head against historical analyst decisions (and against vendor output where we have it) before any vendor commitment is extended.

---

## The problem

- **Alert queues are mostly noise.** Industry experience is that the large majority of transaction-monitoring alerts are false positives. Analysts spend much of their time documenting why something is *fine*.
- **Vendor AI is slow to land.** Onboarding timelines we have seen run one to three years before value shows up, and the result is often a black box layered onto a separate copy of our data.
- **Every vendor adds another island.** Each tool brings its own data pipeline, its own model, its own validation burden, and its own contract — a patchwork instead of a foundation.

## The core idea, as an analogy

Think of hiring a junior investigator.

- The **model** is the investigator's brain.
- The **harness** is everything around the brain: the desk, the case file, the procedures manual, the filing cabinet, the supervisor who signs off, and the timesheet.

Vendors sell a brain packaged with their own proprietary desk. But the brain (a frontier model like Claude) is a commodity we can already reach through Bedrock. **The desk is the part that matters, and it is small enough to own.**

> **Agent = Model + Harness.** The model reasons. The harness decides what it sees, what it may do, what gets recorded, and when it must stop.

## How it works

```
 Alert queue  ──►  Evidence pack  ──►  Investigator  ──►  Validator  ──►  Decision log
 (SQL: group      (SQL: the case      (Claude, with      (only allowed    + impact board
  flagged txns     file — the only     read-only tools,   decisions get    (what it cost,
  into cases)      thing it sees)      step/time budget)  through)          what it saved)
```

1. **Trigger, not a prompt.** The run starts from the existing monitoring queue — flagged activity grouped into one case per customer and typology.
2. **Fixed evidence.** The harness assembles the case file in SQL: the flagged activity, surrounding account activity, KYC notes, prior SARs. The model does not choose its evidence, which makes every decision reproducible.
3. **Bounded investigation.** The model may look up a little more through read-only tools, within a hard limit on steps and seconds.
4. **Constrained decision.** It must return one of `ESCALATE`, `KYC_REVIEW`, or `DISMISS`, with a confidence and a written rationale. Anything else becomes `REVIEW_REQUIRED` and goes to a person.
5. **Durable record.** Every decision is logged with its evidence window and rationale, so an examiner can replay it and the next run can learn from it.

## Design principles that make it bank-grade

| Principle | What it means in practice |
|---|---|
| **The model recommends; a human decides** | Nothing the agent outputs files a SAR or changes a record. |
| **The harness owns the evidence** | Same alert in → same case file → reproducible decision. |
| **The harness owns the vocabulary** | Out-of-policy answers are routed to a human, never guessed. |
| **Read-only by construction** | The agent runs under a SELECT-only database role. |
| **Existing controls apply automatically** | Snowflake row-access and masking policies govern the agent exactly as they govern a person with the same role. |
| **Budgets on every run** | Hard caps on steps, time, and tokens; cost is metered per alert. |
| **Written down twice** | A structured decision log for auditors; a memory the agent recalls on the next run. |
| **Measured in the same place it runs** | The business case comes out of the decision log, with any assumption labelled as one. |

## Build vs. buy — a fair comparison

| | Typical vendor AI tool | In-house investigator |
|---|---|---|
| Time to first value | Long onboarding (often 1–3 years) | Weeks on existing Snowflake data |
| Where data lives | Often copied to vendor environment | Stays in Snowflake; model via our AWS Bedrock account |
| Explainability | Score and reason codes, model opaque | Full evidence pack + written rationale per decision |
| Model risk (SR 11-7) | Vendor model; limited visibility for validation | We own the design, data, prompts, and test results |
| Cost structure | License + implementation + renewals | Model usage (metered per alert) + our team's time |
| Adaptability | Roadmap set by vendor | Change a typology or policy in days |
| Reuse | One tool, one use case | Same harness extends to the next use case |
| **Where vendors are stronger** | Packaged case-management UI, vendor support, existing regulatory track record | We must build UI, support, and documentation ourselves |

The point is not "never buy." It is that **a homegrown base should be the default**, and vendors should earn their place against it on measured results.

## How we will prove it

The test is a **backtest** on alerts our analysts have already worked:

- **Escalation recall** — of the alerts humans escalated, how many did the agent escalate? (The metric that matters most: a missed SAR is the costly error.)
- **Dismissal precision** — of the alerts the agent dismissed, how many did humans also dismiss?
- **Agreement rate** overall and by typology, with disagreements reviewed by an AML SME.
- **Cost and time per alert** from the metered run, against analyst handle time.
- **Rationale quality** — an SME scores a sample of written rationales.
- **Vendor comparison** — the same metrics on the same alerts, where vendor output exists.

**One rule for credibility:** every number comes from the data except at most one, and that one is labelled as an assumption on the page.

## Risks and how the design handles them

| Risk | Mitigation |
|---|---|
| Model is wrong at scale | Human signs every escalation; a standing sample of dismissals is re-reviewed; `REVIEW_REQUIRED` fallback |
| Hallucinated facts | Evidence is assembled by SQL, not retrieved by the model; rationale must cite the evidence |
| Data leaving the bank | Model runs through our AWS Bedrock account; data stays in Snowflake |
| Runaway cost or loops | Per-alert step, time, and token budgets; metered every call |
| Model risk governance | Treat it as a model from day one: documented design, backtest, monitoring plan — MRM engaged early |
| Over-reach | Read-only role; no write path to customer or case systems |

## What a pilot needs

- A **read-only Snowflake role** on alert, transaction, customer, KYC-note, and SAR data (plus a small sandbox schema the agent can write its decision log to)
- **Bedrock access** to a current Claude model in our AWS account
- **Historical alert dispositions** (and analyst handle time, if captured) for the backtest
- A few hours a week from an **AML subject-matter expert** to adjudicate disagreements and score rationales
- Early conversation with **Model Risk Management**

## Where it stands today

A working proof of concept runs on **Cairn Bank**, a fully synthetic bank (860 customers, ~49,000 transactions, 60 alerts across five money-laundering typologies). Unlike typical demos, half of Cairn Bank's alerts are benign look-alikes with documented explanations — the same false-positive problem real queues have — and every alert carries the decision a human analyst would make, so the backtest runs the same way it will on real data. Moving to our environment is a configuration change (DuckDB → Snowflake, Anthropic API → Bedrock), not a rewrite.

## The bigger point

AML triage is the first use case, not the last. The same harness — evidence pack, bounded investigator, validator, decision log, scorecard — applies to KYC refresh, complaint classification, and compliance testing. **Each use case we build in-house makes the next one cheaper. Each vendor we add makes the next integration harder.** That is the case for building the base first.
