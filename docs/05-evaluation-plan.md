# 05 — Evaluation Plan

How we prove (or disprove) that the in-house investigator is good enough — and how it compares to a vendor. This is the document that wins or loses the argument, so it is written to survive a skeptical model-risk reviewer.

---

## The question

> On alerts our analysts have already decided, does the agent reach the same decision, at what cost and speed, and with rationales an expert would accept?

## The design: a backtest

**Analogy (DS):** A holdout evaluation. The human dispositions are the labels; the agent is the model; we never let the model see the labels.

1. Take a set of **closed historical alerts** with human decisions (Cairn Bank: 60 synthetic; Ally: a sample, starting ~50, then a few hundred).
2. **Split** into a dev slice (for prompt tuning) and a holdout slice (for the reported numbers). Never tune on holdout.
3. Run the agent on each alert with **only data available at decision time** (no SARs or notes dated after the alert window).
4. Compare agent decisions to human decisions. Have an SME **adjudicate every disagreement**.
5. Report the scorecard.

## The scorecard

| Metric | Definition | Why it matters | Starting target (to agree with AML + MRM) |
|---|---|---|---|
| **Escalation recall** | Agent ESCALATE or REVIEW_REQUIRED ÷ human ESCALATE | A missed SAR is the costly error | ≥ 95% |
| **Dismissal precision** | Human DISMISS ÷ agent DISMISS | Each correct dismissal is time saved; a wrong one is risk | ≥ 90% |
| **Overall agreement** | Same decision ÷ all alerts | Headline number | Report, don't gate |
| **Agreement by typology** | Per typology | Find where it's weak | Report, don't gate |
| **REVIEW_REQUIRED rate** | Routed to human ÷ all | Knowing what it doesn't know | ≤ 20% |
| **Rationale quality** | SME scores 20 random rationales on a 1–5 rubric (grounded, complete, examiner-ready) | Explainability is the selling point | Mean ≥ 4 |
| **Cost per alert** | Metered tokens × price | Business case | Report |
| **Latency per alert** | Wall-clock seconds | Ops feasibility | Report |
| **Stability** | Same alert run 3× → same decision | Reproducibility | ≥ 90% identical |
| **Proposed vs. final** | Agreement of `proposed_decision` (before the harness's dismissal threshold) vs. final | Shows what the threshold costs/saves | Report |
| **Grounding warning rate** | Decisions with ≥1 ungrounded amount/date ÷ all | Early hallucination signal | Report; review every warning in the pilot |

Treating REVIEW_REQUIRED as "not missed" in escalation recall is deliberate: routing to a human is a safe outcome. It's then counted against the savings via the REVIEW_REQUIRED rate.

## Time saved — measured, not assumed

- **If handle time is captured** (case-management timestamps): time saved = sum of handle time for alerts the agent decided correctly that a human would no longer need to fully work — and state the review overhead you still require (e.g. a 10% dismissal sample).
- **If not:** use exactly one labelled assumption, e.g. `MANUAL_MINUTES_PER_ALERT = 45  # ASSUMPTION`, shown on the board.

Cairn Bank's labels include synthetic handle time (avg: ESCALATE ~116 min, KYC_REVIEW ~54, DISMISS ~26) so the measured path is exercised locally. **Implemented in the impact board (Block 7)**, which reads only `handle_minutes` from the labels — never the human decision.

## The vendor comparison

- **Same alerts, same metrics.** Pull the vendor's score/recommendation for the identical alert set into the labels schema.
- **Threshold fairly.** If the vendor emits a score, choose its threshold on the dev slice (best for the vendor), then report holdout — same rule we apply to ourselves.
- **Add the non-accuracy columns:** onboarding time to date, license + implementation cost, data residency, explainability (does it give a reason per alert?), and validation burden.
- **Locally:** the vendor column is a clearly labelled **stand-in** that only exercises the code path. It is not evidence and must never appear in leadership material as a result.

## Pitfalls we're guarding against

| Pitfall | Guard |
|---|---|
| **Label leakage** | Agent role has no access to labels; `run_sql` refuses the labels schema |
| **Future leakage** | Evidence filtered to before the alert window (SARs, notes, later activity) |
| **Tuning on the test set** | Dev/holdout split; holdout reported |
| **Humans as perfect truth** | SME adjudication of disagreements; report "agent right after adjudication" separately |
| **Cherry-picked typologies** | Report every typology piloted, including weak ones |
| **Synthetic results presented as real** | Cairn Bank numbers labelled synthetic everywhere; leadership sees Ally backtest numbers only |
| **Demo data that flatters** | Cairn Bank is 50% benign by design; an escalate-everything agent scores 33% |

## Honest limits to state up front

- A backtest measures agreement with past human decisions, not ground-truth guilt.
- Performance can drift as typologies, rules, and customer behaviour change — needs ongoing monitoring (dismissal sampling, monthly re-backtest).
- The agent is only as good as the evidence the harness gives it; gaps in source data become gaps in judgment.
- It is a model under SR 11-7 and needs validation like one.

## Output

`main.py backtest` (Block 8, **built**) writes `reports/scorecard.md`: the scorecard table, confusion matrix, per-typology breakdown, cost/latency, and a disagreement list with both the human decision and the agent's rationale side by side for SME review.
