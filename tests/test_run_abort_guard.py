"""Source guard for the run-abort endpoint (v0.16.29).

Pins the load-bearing implementation details:
1. The endpoint sets BOTH the terminal run status AND the in-process
   cancellation event (boundary abort + mid-generation interrupt).
2. The terminal notice is persisted out-of-context via
   create_many_messages_async (capture pattern — never injected into
   the aborted loop's in-context message_ids).
3. Idempotency: terminal-status check precedes the status write.
4. LettaResponse carries run_id and both V3 construction sites
   populate it.

Behavioral coverage: smoke check 7b (abort a multi-step streaming run,
assert cancelled stop_reason + notice + follow-up usability +
idempotency).
"""
from pathlib import Path


def _runs_source() -> str:
    return Path("letta/server/rest_api/routers/v1/runs.py").read_text()


def test_endpoint_sets_status_and_event():
    src = _runs_source()
    assert 'operation_id="abort_run"' in src
    assert "RunUpdate(status=RunStatus.cancelled)" in src
    # Event set directly — mid-generation interrupt without polling latency
    event_pos = src.find("get_cancellation_event_for_run(run_id).set()")
    assert event_pos != -1
    # And the status write happens BEFORE the event set (boundary check reads DB first)
    status_pos = src.find("RunUpdate(status=RunStatus.cancelled)")
    assert status_pos < event_pos


def test_notice_out_of_context():
    src = _runs_source()
    assert "RUN_ABORTED_NOTICE" in src
    assert "create_many_messages_async" in src
    # The capture pattern: no message_ids / in-context mutation in the endpoint
    assert "message_ids" not in src.split("abort_run")[1]


def test_idempotency_terminal_check_first():
    src = _runs_source()
    check_pos = src.find("if run.status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled):")
    write_pos = src.find("RunUpdate(status=RunStatus.cancelled)")
    assert check_pos != -1 and write_pos != -1
    assert check_pos < write_pos, "terminal-status check must precede the status write"


def test_letta_response_run_id_populated():
    resp = Path("letta/schemas/letta_response.py").read_text()
    assert "run_id: Optional[str]" in resp
    v3 = Path("letta/agents/letta_agent_v3.py").read_text()
    # Both construction sites (step() and stream()) populate it
    assert v3.count("run_id=run_id,") >= 2
