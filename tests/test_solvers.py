"""Unit tests for solver providers (mocked HTTP — no network, no keys)."""
import pytest

from chatdeep.errors import TurnstileClientError
from chatdeep.solvers import (
    CapSolverProvider, TwoCaptchaProvider, YesCaptchaProvider, make_solver_provider,
)


class FakeHTTP:
    """Scripted responses for createTask / getTaskResult / getBalance."""

    def __init__(self, script):
        self.script = list(script)   # list of (path -> json) dicts by call order
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json))

        class R:
            status_code = 200

            def __init__(self, payload):
                self._p = payload

            def json(self):
                if isinstance(self._p, Exception):
                    raise ValueError("no json")
                return self._p

            @property
            def text(self):
                return str(self._p)

        payload = self.script.pop(0) if self.script else {"errorId": 1,
                                                          "errorDescription": "script exhausted"}
        return R(payload)

    def close(self):
        pass


def _provider(cls, script, **kw):
    http = FakeHTTP(script)
    p = cls("KEY", http=http, poll_interval=0, **kw)
    return p, http


def test_capsolver_happy_path():
    p, http = _provider(CapSolverProvider, [
        {"errorId": 0, "taskId": "task-1"},
        {"errorId": 0, "status": "processing"},
        {"errorId": 0, "status": "ready", "solution": {"token": "0.TOKEN"}},
    ])
    assert p.take_token() == "0.TOKEN"
    assert http.calls[0][0] == "createTask"
    body = http.calls[0][1]
    assert body["task"]["type"] == "AntiTurnstileTaskProxyLess"
    assert body["task"]["websiteKey"].startswith("0x4AAA")
    assert body["task"]["metadata"] == {"action": "chat"}
    assert http.calls[1][0] == "getTaskResult"


def test_2captcha_task_type():
    p, http = _provider(TwoCaptchaProvider, [
        {"errorId": 0, "taskId": "t2"},
        {"errorId": 0, "status": "ready", "solution": {"token": "TOK"}},
    ])
    assert p.take_token() == "TOK"
    body = http.calls[0][1]["task"]
    assert body["type"] == "TurnstileTaskProxyless"
    assert "metadata" not in body


def test_create_task_error_raises():
    p, _ = _provider(CapSolverProvider, [
        {"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST",
         "errorDescription": "Invalid client key"},
    ])
    with pytest.raises(TurnstileClientError) as ei:
        p.take_token()
    assert "ERROR_KEY_DOES_NOT_EXIST" in str(ei.value)


def test_failed_status_raises():
    p, _ = _provider(YesCaptchaProvider, [
        {"errorId": 0, "taskId": "t3"},
        {"errorId": 0, "status": "failed", "errorCode": "ERROR_TASK",
         "errorDescription": "could not solve"},
    ])
    with pytest.raises(TurnstileClientError):
        p.take_token()


def test_timeout(monkeypatch):
    p, _ = _provider(CapSolverProvider,
                     [{"errorId": 0, "taskId": "t4"}] +
                     [{"errorId": 0, "status": "processing"}] * 50,
                     timeout=0.05)
    with pytest.raises(TurnstileClientError):
        p.take_token()


def test_ready_without_token_raises():
    p, _ = _provider(CapSolverProvider, [
        {"errorId": 0, "taskId": "t5"},
        {"errorId": 0, "status": "ready", "solution": {}},
    ])
    with pytest.raises(TurnstileClientError):
        p.take_token()


def test_get_balance():
    p, _ = _provider(CapSolverProvider, [{"errorId": 0, "balance": 12.5}])
    assert p.get_balance() == 12.5


def test_factory():
    assert isinstance(make_solver_provider("capsolver", "k"), CapSolverProvider)
    assert isinstance(make_solver_provider("2captcha", "k"), TwoCaptchaProvider)
    assert isinstance(make_solver_provider("twocaptcha", "k"), TwoCaptchaProvider)
    assert isinstance(make_solver_provider("yescaptcha", "k"), YesCaptchaProvider)
    with pytest.raises(TurnstileClientError):
        make_solver_provider("unknown-service", "k")
    with pytest.raises(TurnstileClientError):
        CapSolverProvider("")  # empty key
