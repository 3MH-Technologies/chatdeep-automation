"""Unit tests for engine selection in build_token_provider (env-driven)."""
import pytest

from chatdeep.bridge import BridgeTokenProvider
from chatdeep.errors import ChatDeepError
from chatdeep.providers import build_token_provider
from chatdeep.solvers import CapSolverProvider, TwoCaptchaProvider
from chatdeep.turnstile import ManualTokenProvider

CLEAN_ENV = ["CHATDEEP_ENGINE", "CHATDEEP_SOLVER", "CHATDEEP_API_KEY",
             "CHATDEEP_CAPSOLVER_KEY", "CHATDEEP_2CAPTCHA_KEY",
             "CHATDEEP_TURNSTILE_TOKEN", "CHATDEEP_BRIDGE_PORT",
             "CHATDEEP_BRIDGE_KEY", "CHATDEEP_BRIDGE_EMBED"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in CLEAN_ENV:
        monkeypatch.delenv(k, raising=False)


def test_explicit_manual():
    p = build_token_provider("manual", token="tok")
    assert isinstance(p, ManualTokenProvider) and p.take_token() == "tok"
    with pytest.raises(ChatDeepError):
        build_token_provider("manual")


def test_auto_prefers_manual_token(monkeypatch):
    monkeypatch.setenv("CHATDEEP_TURNSTILE_TOKEN", "env-tok")
    assert isinstance(build_token_provider(), ManualTokenProvider)


def test_explicit_solver(monkeypatch):
    monkeypatch.setenv("CHATDEEP_API_KEY", "key-1")
    p = build_token_provider("solver", solver="capsolver")
    assert isinstance(p, CapSolverProvider)
    p = build_token_provider(solver="2captcha", api_key="key-2")
    assert isinstance(p, TwoCaptchaProvider)


def test_solver_requires_key():
    with pytest.raises(ChatDeepError):
        build_token_provider("solver", solver="capsolver")


def test_solver_service_key_env(monkeypatch):
    monkeypatch.setenv("CHATDEEP_CAPSOLVER_KEY", "svc-key")
    monkeypatch.setenv("CHATDEEP_SOLVER", "capsolver")
    assert isinstance(build_token_provider(), CapSolverProvider)


def test_bridge_engine(monkeypatch):
    monkeypatch.setenv("CHATDEEP_BRIDGE_PORT", "9999")
    p = build_token_provider()
    assert isinstance(p, BridgeTokenProvider) and p.base.endswith(":9999")
    p.close()
    monkeypatch.setenv("CHATDEEP_BRIDGE_EMBED", "1")
    p = build_token_provider("bridge", bridge_port=0)
    assert isinstance(p, BridgeTokenProvider)
    p.close()


def test_unknown_engine():
    with pytest.raises(ChatDeepError):
        build_token_provider("magic")


def test_auto_without_anything_is_actionable(monkeypatch):
    # playwright is installed in this environment, so auto → playwright;
    # to test the guidance path we simulate ImportError.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "playwright":
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ChatDeepError) as ei:
        build_token_provider()
    assert "CHATDEEP_SOLVER" in str(ei.value) and "CHATDEEP_BRIDGE_PORT" in str(ei.value)
