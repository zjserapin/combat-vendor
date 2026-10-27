"""Block 8, pure parts: the split, the metric definitions, and the vendor threshold fit."""

import math
import random

import pytest

from src.backtest.data import BacktestCase, Repeat
from src.backtest.metrics import (
    Rate,
    agent_extras,
    disagreements,
    meets_target,
    score_slice,
    stability,
    wilson_interval,
)
from src.backtest.split import DEV, HOLDOUT, assign_slices
from src.backtest.vendor import fit_thresholds, standin_score, to_decision

E, K, D, R = "ESCALATE", "KYC_REVIEW", "DISMISS", "REVIEW_REQUIRED"


def _case(alert_id, human, agent, typology="STRUCTURING", **extra):
    return BacktestCase(alert_id=alert_id, typology=typology, human_decision=human,
                        human_rationale="analyst says", agent_decision=agent, **extra)


# --- split ---------------------------------------------------------------------------------------

ALERTS = [(f"a{i}", f"T{i % 3}|{('E', 'K', 'D')[i % 2]}") for i in range(60)]


def test_split_is_deterministic_and_ignores_row_order():
    shuffled = ALERTS[:]
    random.Random(7).shuffle(shuffled)
    assert assign_slices(ALERTS, 0.5, "s") == assign_slices(shuffled, 0.5, "s")


def test_split_covers_every_alert_and_balances_each_stratum():
    slices = assign_slices(ALERTS, 0.5, "s")
    assert set(slices) == {a for a, _ in ALERTS}
    for stratum in {s for _, s in ALERTS}:
        members = [slices[a] for a, s in ALERTS if s == stratum]
        assert members.count(HOLDOUT) == len(members) // 2


def test_salt_redraws_the_split():
    assert assign_slices(ALERTS, 0.5, "one") != assign_slices(ALERTS, 0.5, "two")


def test_split_edges():
    assert set(assign_slices(ALERTS, 0.0, "s").values()) == {DEV}
    assert set(assign_slices(ALERTS, 1.0, "s").values()) == {HOLDOUT}
    assert assign_slices([("only", "x")], 0.5, "s") == {"only": HOLDOUT}  # rounds half up
    with pytest.raises(ValueError):
        assign_slices(ALERTS, 1.5, "s")


# --- metric definitions --------------------------------------------------------------------------

def test_slice_metrics_follow_the_evaluation_plan():
    m = score_slice([
        ("T1", E, E), ("T1", E, R), ("T1", E, D),  # recall: E and R count as caught -> 2/3
        ("T2", D, D), ("T2", D, D), ("T2", K, D),  # precision: agent dismissed 4 (incl. T1's
        ("T2", K, K), ("T2", D, R),                 # missed escalation), 2 of them right
    ])
    assert (m.escalation_recall.hits, m.escalation_recall.total) == (2, 3)
    assert (m.dismissal_precision.hits, m.dismissal_precision.total) == (2, 4)
    assert (m.agreement.hits, m.agreement.total) == (4, 8)
    assert (m.review_required.hits, m.review_required.total) == (2, 8)
    assert m.by_typology["T1"] == Rate(1, 3) and m.by_typology["T2"] == Rate(3, 5)
    assert m.confusion[(K, D)] == 1 and sum(m.confusion.values()) == 8


def test_nothing_to_measure_is_not_zero():
    m = score_slice([("T", K, K)])
    assert m.escalation_recall.value is None and m.dismissal_precision.value is None
    assert meets_target("escalation_recall", m.escalation_recall) is None


def test_wilson_interval_is_honest_at_small_n():
    lo, hi = wilson_interval(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.722, abs=0.001)
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.278, abs=0.001)
    assert Rate(3, 4).interval[0] < 0.75 < Rate(3, 4).interval[1]


def test_targets():
    assert meets_target("escalation_recall", Rate(19, 20))
    assert not meets_target("escalation_recall", Rate(18, 20))
    assert meets_target("review_required", Rate(2, 10))
    assert not meets_target("review_required", Rate(3, 10))


def test_disagreements_worst_first_and_review_required_excluded():
    cases = [_case("over", D, E), _case("routed", E, R), _case("same", K, K),
             _case("missed", E, D), _case("under", E, K), _case("not-run", E, None)]
    found = disagreements(cases)
    assert [(d.case.alert_id, d.kind) for d in found] == [
        ("missed", "MISSED ESCALATION"), ("under", "under-called"), ("over", "over-called")]


def test_extras_separate_the_model_from_the_policy():
    reason = "DISMISS at confidence 0.40 is below the 0.60 threshold for dismissals"
    cases = [
        _case("held-right", D, R, proposed_decision=D, review_reason=reason),
        _case("held-saved", E, R, proposed_decision=D, review_reason=reason),
        _case("ok", E, E, proposed_decision=E, warnings=["$1 not found"]),
        _case("invalid", K, R, review_reason="validator: bad confidence"),
    ]
    x = agent_extras(cases)
    assert x.threshold_holds == 2 and x.threshold_holds_human_dismiss == 1
    assert (x.proposed_agreement.hits, x.proposed_agreement.total) == (2, 4)
    assert x.warnings_when_agreed == Rate(1, 1) and x.warnings_when_disagreed == Rate(0, 3)


def test_stability_scoped_to_the_slice():
    repeats = [Repeat("a", 3, True), Repeat("b", 2, False), Repeat("c", 2, True)]
    assert stability(repeats, ["a", "b"]) == Rate(1, 2)
    assert stability(repeats, ["z"]).value is None


# --- vendor --------------------------------------------------------------------------------------

def test_thresholds_fit_the_dev_data():
    dev = [(0.9, E), (0.8, E), (0.5, K), (0.45, K), (0.2, D), (0.1, D)]
    escalate_at, review_at = fit_thresholds(dev)
    assert [to_decision(s, escalate_at, review_at) for s, _ in dev] == [h for _, h in dev]


def test_thresholds_with_no_dev_data_never_escalate():
    escalate_at, review_at = fit_thresholds([])
    assert math.isinf(escalate_at) and to_decision(1.0, escalate_at, review_at) == D


def test_standin_score_is_bounded_and_monotone():
    low = standin_score(10, 0, 100_000)
    assert 0 <= low < standin_score(90, 0, 100_000) < standin_score(90, 1, 100_000) <= 1
    assert standin_score(100, 1, 10**12) == 1.0
