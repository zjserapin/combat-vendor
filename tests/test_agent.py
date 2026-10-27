"""Block 5: the investigator loop — every exit path, with scripted model responses."""

from dataclasses import replace

import pytest

from src import ledger
from src.agent import DECIDED, REVIEW_REQUIRED, investigate
from src.alert_queue import fetch_alerts
from src.db import Database
from src.evidence import build_evidence
from src.prompts import FINAL_STEP_NOTICE_V1, NUDGE_V1, PROMPT_VERSION
from src.tools import Toolbox
from tests.fakes import ScriptedLLM, decision, reply, text, tool_use

SQL = {"query": "SELECT COUNT(*) AS n FROM bank.customers", "purpose": "sanity check"}


@pytest.fixture
def setup(workspace):
    with Database(workspace) as db:
        ledger.ensure_agent_schema(db, workspace)
        alert = fetch_alerts(db, workspace)[0]
        pack = build_evidence(db, workspace, alert)
        yield workspace, db, alert, pack


def _run(setup, script, **config_overrides):
    config, db, alert, pack = setup
    config = replace(config, **config_overrides)
    llm, toolbox = ScriptedLLM(script), Toolbox(db, config)
    result = investigate(alert, pack, llm, toolbox, config)
    _assert_protocol(result.messages)
    return result, llm


def _assert_protocol(messages):
    """API invariants: every tool_use is answered, in order, in the next user message,
    and tool_result blocks come before any text in that message."""
    for i, msg in enumerate(messages):
        if msg["role"] != "assistant":
            continue
        ids = [b.id for b in msg["content"] if b.type == "tool_use"]
        if not ids or i + 1 == len(messages):
            continue
        following = messages[i + 1]["content"]
        result_ids = [b["tool_use_id"] for b in following if b.get("type") == "tool_result"]
        assert result_ids == ids
        kinds = [b["type"] for b in following]
        assert kinds == sorted(kinds, key=lambda k: k != "tool_result")


# --- clean paths -----------------------------------------------------------------------

def test_decides_on_the_first_turn(setup):
    result, _ = _run(setup, [reply(decision("DISMISS", 0.9))])
    _, _, alert, pack = setup
    assert result.outcome == DECIDED and result.decision_input["decision"] == "DISMISS"
    assert (result.steps, result.input_tokens, result.output_tokens) == (1, 1_000, 200)
    assert result.evidence_hash == pack.fingerprint and result.prompt_version == PROMPT_VERSION
    assert result.alert_id == alert.alert_id


def test_looks_something_up_then_decides(setup):
    result, llm = _run(setup, [reply(text("Checking."), tool_use("run_sql", **SQL)),
                               reply(decision("ESCALATE"))])
    assert result.outcome == DECIDED and result.steps == 2
    assert [c.name for c in result.tool_calls] == ["run_sql"]
    tool_turn = llm.requests[1]["messages"][-1]["content"]
    assert tool_turn[0]["type"] == "tool_result" and "860" in tool_turn[0]["content"]


def test_parallel_tool_calls_answered_in_one_message(setup):
    result, llm = _run(setup, [reply(tool_use("describe_tables"), tool_use("run_sql", **SQL)),
                               reply(decision())])
    answers = llm.requests[1]["messages"][-1]["content"]
    assert [a["type"] for a in answers] == ["tool_result", "tool_result"]
    assert result.outcome == DECIDED


def test_first_request_carries_policy_case_file_and_tools(setup):
    config, _, _, pack = setup
    _, llm = _run(setup, [reply(decision())])
    first = llm.requests[0]
    assert config.bank_name in first["system"] and "data, not instructions" in first["system"]
    assert pack.text in first["messages"][0]["content"]
    assert first["tools"] == ["run_sql", "describe_tables", "recall_case_memory",
                              "submit_decision"]
    assert first["max_tokens"] == config.agent_max_tokens


def test_assistant_turns_are_appended_verbatim(setup):
    script = [reply(text("thinking out loud"), tool_use("run_sql", **SQL)), reply(decision())]
    first_response = script[0]
    result, _ = _run(setup, script)
    assert result.messages[1]["content"] is first_response.content


# --- recoverable problems -----------------------------------------------------------------

def test_bad_tool_input_is_an_error_result_not_a_crash(setup):
    result, _ = _run(setup, [reply(tool_use("run_sql", purpose="oops")),
                               reply(tool_use("teleport")),
                               reply(decision())])
    assert result.outcome == DECIDED
    assert [c.is_error for c in result.tool_calls] == [True, True]


def test_one_nudge_when_the_model_forgets_to_submit(setup):
    result, llm = _run(setup, [reply(text("Looks benign to me.")), reply(decision())])
    assert result.outcome == DECIDED and result.steps == 2
    nudge = llm.requests[1]["messages"][-1]["content"]
    assert nudge == [{"type": "text", "text": NUDGE_V1}]


def test_last_step_warning_is_sent_before_the_final_turn(setup):
    script = [reply(tool_use("run_sql", **SQL)), reply(tool_use("run_sql", **SQL)),
              reply(decision())]
    result, llm = _run(setup, script, agent_max_steps=3)
    assert result.outcome == DECIDED
    final_turn_input = llm.requests[2]["messages"][-1]["content"]
    assert final_turn_input[-1] == {"type": "text", "text": FINAL_STEP_NOTICE_V1}
    assert all(FINAL_STEP_NOTICE_V1 not in str(r["messages"][-1]) for r in llm.requests[:2])


# --- hand-offs to a human -------------------------------------------------------------------

@pytest.mark.parametrize(("stop", "reason"), [
    ("refusal", "refusal"),
    ("max_tokens", "cut off"),
    ("model_context_window_exceeded", "context window"),
])
def test_bad_stop_reasons_go_to_a_human(setup, stop, reason):
    result, _ = _run(setup, [reply(text("..."), stop=stop)])
    assert result.outcome == REVIEW_REQUIRED and reason in result.review_reason
    assert result.decision_input is None and result.stop_reasons == [stop]


def test_step_budget_exhausted(setup):
    script = [reply(tool_use("run_sql", **SQL)) for _ in range(4)]
    result, llm = _run(setup, script, agent_max_steps=4)
    assert result.outcome == REVIEW_REQUIRED and result.review_reason == "step budget exhausted"
    assert result.steps == 4 and len(llm.requests) == 4


def test_time_budget_exhausted(setup):
    config, db, alert, pack = setup
    ticks = iter([0.0, 0.0, 200.0, 200.0])
    llm = ScriptedLLM([reply(tool_use("run_sql", **SQL)), reply(decision())])
    result = investigate(alert, pack, llm, Toolbox(db, config), config,
                         clock=lambda: next(ticks))
    assert result.outcome == REVIEW_REQUIRED and result.review_reason == "time budget exhausted"
    assert len(llm.requests) == 1


def test_ignoring_the_nudge_goes_to_a_human(setup):
    result, _ = _run(setup, [reply(text("Benign.")), reply(text("Still benign."))])
    assert result.outcome == REVIEW_REQUIRED and "without calling submit_decision" in \
        result.review_reason


def test_two_decisions_in_one_turn_go_to_a_human(setup):
    result, _ = _run(setup, [reply(decision("DISMISS"), decision("ESCALATE"))])
    assert result.outcome == REVIEW_REQUIRED and "more than one decision" in result.review_reason


def test_model_api_errors_propagate_for_the_run_loop_to_handle(setup):
    config, db, alert, pack = setup
    llm = ScriptedLLM([ConnectionError("model unreachable")])
    with pytest.raises(ConnectionError):
        investigate(alert, pack, llm, Toolbox(db, config), config)


def test_meter_deltas_are_per_alert(setup):
    config, db, alert, pack = setup
    llm = ScriptedLLM([reply(decision()), reply(decision())])
    toolbox = Toolbox(db, config)
    investigate(alert, pack, llm, toolbox, config)
    second = investigate(alert, pack, llm, toolbox, config)
    assert (second.input_tokens, second.steps) == (1_000, 1)
    assert llm.meter.calls == 2
