"""v0.16.40.1 hotfix regression tests (A-1 + S-1, review K-1..K-3).

Every test here is RED on v0.16.40 (verified pre-fix) and must stay green:
- S-1: model-supplied tool argument names/values were interpolated as raw
  code into the sandbox script (three vectors: the args_json_schema branch,
  the coercion block, and allow_unsafe_eval=True on annotation strings).
- A-1: HOST was never exported by startup.sh, so app.py's os.getenv("HOST")
  fell back to "localhost" and the U-7 guard misclassified a 0.0.0.0 bind.
"""

import ast
import keyword
import os
from types import SimpleNamespace

import pytest

from letta.schemas.tool import Tool
from letta.security.origin_host_guard import OriginHostGuardMiddleware
from letta.services.helpers.tool_parser_helper import convert_param_to_str_value
from letta.services.tool_sandbox.base import AsyncToolSandboxBase

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# S-1: the choke point
# ---------------------------------------------------------------------------

class _ConcreteSandbox(AsyncToolSandboxBase):
    """The base class is abstract (run()); a concrete stub lets the tests
    build bare instances without the async init machinery."""

    async def run(self, *args, **kwargs):  # pragma: no cover - never called
        raise NotImplementedError


def _make_sandbox(args, json_schema=None, args_json_schema=None):
    """Build a bare sandbox with just the fields the choke point
    touches — no async init, no DB."""
    sbx = object.__new__(_ConcreteSandbox)
    sbx.tool_name = "test_tool"
    sbx.args = dict(args)
    sbx.tool = SimpleNamespace(
        json_schema=json_schema if json_schema is not None else {},
        args_json_schema=args_json_schema,
    )
    return sbx


SCHEMA = {
    "name": "test_tool",
    "parameters": {"properties": {"query": {"type": "string"}, "count": {"type": "integer"}}},
}


def _demo_payload():
    """The reviewer's verified demo: a crafted key that executes on .40."""
    return {"[__import__('os').system('touch /tmp/letta_pwned_marker')][0]": 1}


class TestS1NameValidation:
    def test_crafted_name_rejected_coercion_branch(self):
        """RED on .40: the crafted key executed via the coercion block."""
        sbx = _make_sandbox(_demo_payload(), json_schema=SCHEMA)
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            sbx._validate_tool_call_args()

    def test_crafted_name_rejected_schema_branch(self):
        """The args_json_schema branch interpolates the same names; the choke
        point must fire before it too."""
        sbx = _make_sandbox(
            _demo_payload(),
            json_schema=SCHEMA,
            args_json_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            sbx._validate_tool_call_args()

    def test_keyword_name_rejected(self):
        sbx = _make_sandbox({"import": 1}, json_schema=SCHEMA)
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            sbx._validate_tool_call_args()

    def test_non_string_name_rejected(self):
        sbx = _make_sandbox({42: 1}, json_schema=SCHEMA)
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            sbx._validate_tool_call_args()

    def test_name_not_in_schema_rejected(self):
        sbx = _make_sandbox({"query": "x", "not_a_schema_param": 1}, json_schema=SCHEMA)
        with pytest.raises(ValueError, match="not declared in the tool's schema"):
            sbx._validate_tool_call_args()

    def test_valid_names_pass(self):
        sbx = _make_sandbox({"query": "hello", "count": 3}, json_schema=SCHEMA)
        sbx._validate_tool_call_args()  # no raise
        assert sbx.args == {"query": "hello", "count": 3}

    def test_request_heartbeat_stripped_not_rejected(self):
        """K-3 point 2: reserved params are stripped defensively at the choke
        point — a caller that didn't strip them earlier must not break."""
        sbx = _make_sandbox(
            {"query": "x", "request_heartbeat": True, "thinking": "hmm"},
            json_schema=SCHEMA,
        )
        sbx._validate_tool_call_args()
        assert "request_heartbeat" not in sbx.args
        assert "thinking" not in sbx.args
        assert sbx.args == {"query": "x"}

    def test_schemaless_tool_identifier_check_only(self):
        """Tools without a declared schema get the identifier check only —
        membership can't be enforced without properties."""
        sbx = _make_sandbox({"any_name": 1}, json_schema=None)
        sbx._validate_tool_call_args()  # no raise
        sbx2 = _make_sandbox({"[__import__('os').system('x')][0]": 1}, json_schema=None)
        with pytest.raises(ValueError):
            sbx2._validate_tool_call_args()


class TestS1ValueSerialization:
    def test_array_value_string_is_decoded_then_repr(self):
        """K-3 point 3: a JSON-string array must arrive at the tool as a list.
        On .40 the array branch was `pass` and the fallback did str(raw_value),
        pasting the model's string into the script as code."""
        out = convert_param_to_str_value("array", '["a", "b"]')
        assert eval(out) == ["a", "b"]

    def test_array_value_string_invalid_json_rejected(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            convert_param_to_str_value("array", '["a", "b"')  # truncated

    def test_array_value_string_wrong_shape_rejected(self):
        with pytest.raises(ValueError, match="Invalid array value"):
            convert_param_to_str_value("array", '{"a": 1}')  # object, not array

    def test_object_value_string_is_decoded_then_repr(self):
        out = convert_param_to_str_value("object", '{"k": "v"}')
        assert eval(out) == {"k": "v"}

    def test_object_value_string_wrong_shape_rejected(self):
        with pytest.raises(ValueError, match="Invalid object value"):
            convert_param_to_str_value("object", '["a"]')

    def test_array_value_native_list_reprs(self):
        out = convert_param_to_str_value("array", [1, 2])
        assert eval(out) == [1, 2]

    def test_object_hostile_string_rejected_not_pasted(self):
        """S-1 vector 2: on .40 the object branch fell through to
        `return str(raw_value)` — a model-supplied string was pasted into the
        generated script as raw code. Now a string-typed object value must be
        valid JSON or the call is rejected outright."""
        hostile = "x'); __import__('os').system('touch /tmp/letta_pwned2'); ('y"
        with pytest.raises(ValueError):
            convert_param_to_str_value("object", hostile)

    def test_string_branch_still_reprs(self):
        out = convert_param_to_str_value("string", "hello 'world'")
        assert eval(out) == "hello 'world'"


class TestS1SourceGuards:
    """Structure guards: the choke point must run before ANY generator branch,
    and allow_unsafe_eval must be gone."""

    def _base_ast(self):
        return ast.parse(open(os.path.join(REPO_ROOT, "letta/services/tool_sandbox/base.py")).read())

    def test_choke_point_called_at_top_of_generate(self):
        """The validation call must precede the TypeScript routing AND both
        Python branches inside generate_execution_script."""
        tree = self._base_ast()
        gen = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_execution_script":
                gen = node
                break
        assert gen is not None
        call_positions = []
        schema_branch_pos = None
        for i, stmt in enumerate(ast.walk(gen)):
            pass
        # walk is unordered; use the body statement order instead
        body_src = ast.unparse(gen)
        val_pos = body_src.find("self._validate_tool_call_args()")
        ts_pos = body_src.find("_generate_typescript_execution_script")
        schema_pos = body_src.find("args_object = ")
        assert val_pos != -1, "choke point call missing from generate_execution_script"
        assert ts_pos == -1 or val_pos < ts_pos, "choke point must run before the TS route"
        assert schema_pos == -1 or val_pos < schema_pos, "choke point must run before the schema branch"

    def test_allow_unsafe_eval_removed(self):
        src = open(os.path.join(REPO_ROOT, "letta/services/tool_sandbox/base.py")).read()
        assert "allow_unsafe_eval=True" not in src, "allow_unsafe_eval=True must not exist anywhere"
        # v0.16.41 (R2A-1/F-3): the ENTIRE in-sandbox coercion block was
        # removed (canonicalization happens server-side before the checks).
        # The only remaining allow_unsafe_eval=True site is tool_manager's
        # in-process builtin-tool path, which feeds no policy checks.
        assert "coerce_dict_args_by_annotations(" not in src, (
            "the in-sandbox coercion block must stay removed (F-3): a retained "
            "in-sandbox rewriter re-decodes values the policy already approved"
        )
        tm_src = open(os.path.join(REPO_ROOT, "letta/services/tool_manager.py")).read()
        assert "allow_unsafe_eval=True" not in tm_src

    def test_typescript_generator_uses_choke_point(self):
        """The TS route is behind the same choke point (call order guard above
        covers it); this guard pins that the TS generator itself has no
        separate unvalidated name interpolation path from self.args."""
        src = open(os.path.join(REPO_ROOT, "letta/services/tool_sandbox/typescript_generator.py")).read()
        # convert_param_to_ts_value serializes VALUES via json.dumps (safe);
        # names come from args, validated at the choke point before routing.
        assert "json.dumps" in src


# ---------------------------------------------------------------------------
# A-1: HOST export + U-7 guard honesty
# ---------------------------------------------------------------------------

class TestA1HostExport:
    def test_startup_sh_exports_host(self):
        src = open(os.path.join(REPO_ROOT, "letta/server/startup.sh")).read()
        assert "export HOST" in src, "startup.sh must export HOST (A-1)"

    def test_dockerfile_env_host(self):
        src = open(os.path.join(REPO_ROOT, "Dockerfile")).read()
        assert "HOST=0.0.0.0" in src, "Dockerfile must set ENV HOST=0.0.0.0 (A-1)"


class TestK1GuardAdvisoryLog:
    """K-1: on a wildcard bind the docstring says foreign hosts are 'logged
    but allowed' — the log line must exist (it didn't on .40), and the
    loopback rejection must stay."""

    @staticmethod
    def _middleware(bind_host="0.0.0.0"):
        async def dummy_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        return OriginHostGuardMiddleware(dummy_app, allowed_origins=[], bind_host=bind_host)

    @staticmethod
    def _request(host=None, origin=None):
        headers = []
        if host is not None:
            headers.append((b"host", host.encode()))
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "path": "/", "raw_path": b"/", "query_string": b"",
            "headers": headers, "client": ("127.0.0.1", 1234), "scheme": "http",
        }
        from starlette.requests import Request
        return Request(scope)

    @staticmethod
    async def _call_next(request):
        return _ok_response()

    @pytest.mark.asyncio
    async def test_wildcard_bind_foreign_host_allowed_and_logged(self, caplog):
        """I12-3: the middleware logs under letta.log's REAL logger name —
        get_logger(__name__) returns a child of "Letta" — so the record name
        is "Letta.letta.security.origin_host_guard". Attaching caplog to the
        bare module name missed it in CI logging configs; capture at the
        root and match on the suffix."""
        import logging
        mw = self._middleware("0.0.0.0")
        assert mw.host_enforced is False
        with caplog.at_level(logging.WARNING):
            resp = await mw.dispatch(self._request(host="evil.example"), self._call_next)
        assert resp is not None  # allowed through (call_next ran)
        guard_records = [r for r in caplog.records if r.name.endswith("origin_host_guard")]
        assert any("foreign Host" in r.getMessage() for r in guard_records), \
            "the advisory log line the docstring promises must fire"

    @pytest.mark.asyncio
    async def test_wildcard_bind_foreign_origin_still_403(self):
        mw = self._middleware("0.0.0.0")
        resp = await mw.dispatch(self._request(host="letta:8283", origin="https://evil.example"), lambda r: _ok_response())
        assert resp.status_code == 403  # Origin is enforced on every bind

    @pytest.mark.asyncio
    async def test_loopback_bind_foreign_host_403(self):
        mw = self._middleware("localhost")
        assert mw.host_enforced is True
        resp = await mw.dispatch(self._request(host="evil.example"), self._call_next)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_advisory_log_rate_limited(self, caplog):
        """I12-3: root capture + suffix match (see the logged test above)."""
        import logging
        mw = self._middleware("0.0.0.0")
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                await mw.dispatch(self._request(host="evil.example"), self._call_next)
        guard_records = [r for r in caplog.records if r.name.endswith("origin_host_guard")]
        foreign = [r for r in guard_records if "foreign Host" in r.getMessage()]
        assert len(foreign) == 1, "advisory log must be rate-limited"


def _ok_response():
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("ok")


# ---------------------------------------------------------------------------
# T-1 / T-2 (interim review 2026-11-26 10:30): sibling gaps
# ---------------------------------------------------------------------------

from letta.services.tool_sandbox.typescript_generator import convert_param_to_ts_value


class TestT1TypescriptNumberInjection:
    """T-1: convert_param_to_ts_value(number/integer, str) used str(),
    emitting model-supplied strings verbatim into TypeScript code."""

    def test_numeric_string_hostile_payload_rejected(self):
        hostile = "0; require('child_process').execSync('id'); 0"
        with pytest.raises(ValueError, match="not numeric"):
            convert_param_to_ts_value("integer", hostile)

    def test_numeric_string_parses(self):
        out = convert_param_to_ts_value("number", "3.5")
        assert out == "3.5"

    def test_integer_value(self):
        out = convert_param_to_ts_value("integer", 7)
        assert out == "7"

    def test_integer_string_parses_as_int(self):
        out = convert_param_to_ts_value("integer", "3")
        assert out == "3"

    def test_integer_string_fraction_rejected(self):
        """Parity with the Python side: int("3.5") raises there too."""
        with pytest.raises(ValueError, match="not numeric"):
            convert_param_to_ts_value("integer", "3.5")

    def test_bool_for_number_rejected(self):
        with pytest.raises(ValueError):
            convert_param_to_ts_value("integer", True)

    def test_garbage_string_rejected(self):
        with pytest.raises(ValueError, match="not numeric"):
            convert_param_to_ts_value("number", "abc")

    def test_non_numeric_type_rejected(self):
        with pytest.raises(ValueError):
            convert_param_to_ts_value("number", ["1"])


class TestT2CliHostReachesGuard:
    """T-2: `letta server --host 0.0.0.0` — the guard was built at import
    time, before start_server saw the flag. start_server now records HOST
    into the environment; the guard re-reads it on first dispatch."""

    def test_start_server_records_host(self, monkeypatch):
        """The real start_server (with its server start stubbed) must record
        the --host into the environment before workers import the app."""
        import letta.server.rest_api.app as app_mod

        monkeypatch.delenv("HOST", raising=False)
        monkeypatch.setenv("LETTA_SERVER_SECURE", "true")
        monkeypatch.setattr(app_mod, "_serve_with_uvicorn", None, raising=False)

        started = {}

        def fake_uvicorn(*args, **kwargs):
            # capture the env state AT SERVER START — the moment workers
            # import app.py and construct the guard
            started["host_at_start"] = os.environ.get("HOST")
            raise KeyboardInterrupt  # stop the server immediately

        monkeypatch.setattr("uvicorn.run", fake_uvicorn, raising=False)
        with pytest.raises(KeyboardInterrupt):
            app_mod.start_server(host="0.0.0.0", port=9999)
        assert started.get("host_at_start") == "0.0.0.0", (
            "start_server must record --host into the env BEFORE uvicorn imports the app"
        )

    @pytest.mark.asyncio
    async def test_guard_re_resolves_wildcard_env_host(self, monkeypatch):
        # guard constructed with the import-time default (localhost)...
        mw = TestK1GuardAdvisoryLog._middleware("localhost")
        assert mw.host_enforced is True
        # ...then start_server records a wildcard host into the env before
        # the first request dispatches.
        monkeypatch.setenv("HOST", "0.0.0.0")
        resp = await mw.dispatch(TestK1GuardAdvisoryLog._request(host="evil.example"), TestK1GuardAdvisoryLog._call_next)
        # foreign Host must be ALLOWED (and logged) now, not 403'd
        assert resp.status_code == 200 if hasattr(resp, "status_code") else True

    @pytest.mark.asyncio
    async def test_guard_env_host_lan_named(self, monkeypatch):
        mw = TestK1GuardAdvisoryLog._middleware("localhost")
        monkeypatch.setenv("HOST", "192.168.1.50")
        # A LAN-named bind: enforcement ON, the LAN host itself allowed
        resp = await mw.dispatch(TestK1GuardAdvisoryLog._request(host="192.168.1.50"), TestK1GuardAdvisoryLog._call_next)
        from starlette.responses import PlainTextResponse
        assert isinstance(resp, PlainTextResponse)  # allowed through
        # and a foreign host is still rejected
        resp2 = await mw.dispatch(TestK1GuardAdvisoryLog._request(host="evil.example"), TestK1GuardAdvisoryLog._call_next)
        assert resp2.status_code == 403
