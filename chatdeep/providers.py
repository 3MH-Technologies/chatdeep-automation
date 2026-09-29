"""Token-provider factory with environment-based auto-detection.

Engine priority (``auto``):

1. ``manual``    — a token supplied directly (``CHATDEEP_TURNSTILE_TOKEN`` /
                   ``--token``); single use, for tests.
2. ``solver``    — commercial solving API (``CHATDEEP_SOLVER`` +
                   ``CHATDEEP_API_KEY`` or per-service key env).  No browser.
3. ``bridge``    — local token bridge fed by your own browser via the
                   userscript (``CHATDEEP_BRIDGE_PORT``).  No browser *in
                   Python*.  ``CHATDEEP_BRIDGE_EMBED=1`` starts the server
                   in-process.
4. ``playwright``— last resort: headless Chromium (requires
                   ``pip install playwright`` + ``playwright install chromium``).

Everything is configurable explicitly via :func:`build_token_provider`.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .bridge import DEFAULT_PORT, BridgeTokenProvider
from .errors import DependencyError, TurnstileClientError
from .solvers import SOLVERS, make_solver_provider
from .turnstile import (
    DEFAULT_PAGE,
    DEFAULT_SITEKEY,
    ManualTokenProvider,
    PlaywrightTokenProvider,
    TokenProvider,
)

log = logging.getLogger("chatdeep.providers")

ENGINES = ("auto", "manual", "solver", "bridge", "playwright")

#: Per-service key environment variables (``CHATDEEP_API_KEY`` also works).
_KEY_ENV = {
    "capsolver": ("CHATDEEP_CAPSOLVER_KEY", "CAPSOLVER_API_KEY"),
    "2captcha": ("CHATDEEP_2CAPTCHA_KEY", "TWOCAPTCHA_API_KEY"),
    "yescaptcha": ("CHATDEEP_YESCAPTCHA_KEY",),
}


def _solver_key(service: str) -> Optional[str]:
    names = _KEY_ENV.get(service, ())
    for name in (*names, "CHATDEEP_API_KEY"):
        val = os.environ.get(name)
        if val:
            return val
    return None


def _make_solver(solver_name: str, api_key: Optional[str], sitekey: str) -> TokenProvider:
    if not solver_name:
        raise TurnstileClientError(
            f"محرك solver يتطلب تحديد الخدمة (CHATDEEP_SOLVER أو --solver؛ "
            f"المدعوم: {', '.join(SOLVERS)})", code="dsc_turnstile_client")
    key = api_key or _solver_key(solver_name)
    if not key:
        raise TurnstileClientError(
            f"لا يوجد مفتاح API لخدمة {solver_name} — اضبط CHATDEEP_API_KEY "
            f"أو {_KEY_ENV.get(solver_name, ('CHATDEEP_<SERVICE>_KEY',))[0]} أو --api-key",
            code="dsc_turnstile_client")
    log.info("token engine: solver/%s", solver_name)
    return make_solver_provider(solver_name, key, website_key=sitekey)


def _make_bridge(host: str, port: Optional[int], key: Optional[str],
                 embed: Optional[bool]) -> TokenProvider:
    env_port = os.environ.get("CHATDEEP_BRIDGE_PORT")
    final_port = port or int(env_port or DEFAULT_PORT)
    final_key = key or os.environ.get("CHATDEEP_BRIDGE_KEY") or None
    final_embed = embed if embed is not None else \
        os.environ.get("CHATDEEP_BRIDGE_EMBED", "").lower() in ("1", "true", "yes")
    log.info("token engine: bridge (port=%d, embed=%s)", final_port, final_embed)
    return BridgeTokenProvider(host=host, port=final_port, key=final_key, embed=final_embed)


def _make_playwright(headless: bool, sitekey: str, page_url: str) -> TokenProvider:
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise DependencyError(
            "Playwright غير مثبت — نفّذ: pip install playwright && "
            "python -m playwright install chromium",
            code="dsc_turnstile_client")
    log.info("token engine: playwright (headless=%s)", headless)
    return PlaywrightTokenProvider(sitekey=sitekey, page_url=page_url, headless=headless)


NO_ENGINE_GUIDANCE = (
    "لا يوجد محرك توكن مضبوط. الخيارات بدون متصفح:\n"
    "  1) خدمة حل برمجية: CHATDEEP_SOLVER=capsolver CHATDEEP_API_KEY=...  (أو --solver/--api-key)\n"
    "  2) جسر محلي مجاني: CHATDEEP_BRIDGE_PORT=8787 مع سكربت المتصفح (chatdeep userscript)\n"
    "  3) توكن يدوية:     CHATDEEP_TURNSTILE_TOKEN=... (استخدام واحد)\n"
    "  4) متصفح آلي:      pip install playwright && python -m playwright install chromium"
)


def build_token_provider(engine: str = "auto", *,
                         solver: Optional[str] = None,
                         api_key: Optional[str] = None,
                         token: Optional[str] = None,
                         bridge_host: str = "127.0.0.1",
                         bridge_port: Optional[int] = None,
                         bridge_key: Optional[str] = None,
                         bridge_embed: Optional[bool] = None,
                         headless: bool = True,
                         sitekey: str = DEFAULT_SITEKEY,
                         page_url: str = DEFAULT_PAGE) -> TokenProvider:
    """Create the token provider selected by *engine* (or environment).

    Raises:
        DependencyError / TurnstileClientError with Arabic guidance when the
        selected engine is not configured.
    """
    engine = (engine or os.environ.get("CHATDEEP_ENGINE") or "auto").lower()
    if engine not in ENGINES:
        raise TurnstileClientError(
            f"محرك غير معروف: {engine} (المدعوم: {', '.join(ENGINES)})",
            code="dsc_turnstile_client")

    manual_token = token or os.environ.get("CHATDEEP_TURNSTILE_TOKEN")
    solver_name = (solver or os.environ.get("CHATDEEP_SOLVER") or "").lower() or None
    env_bridge = bool(os.environ.get("CHATDEEP_BRIDGE_PORT"))

    if engine == "manual":
        if not manual_token:
            raise TurnstileClientError(
                "المحرك manual يتطلب توكن (CHATDEEP_TURNSTILE_TOKEN أو --token)",
                code="dsc_turnstile_client")
        return ManualTokenProvider(manual_token)

    if engine == "solver":
        return _make_solver(solver_name or "", api_key, sitekey)

    if engine == "bridge":
        return _make_bridge(bridge_host, bridge_port, bridge_key, bridge_embed)

    if engine == "playwright":
        return _make_playwright(headless, sitekey, page_url)

    # ---- auto ----
    if manual_token:
        log.info("token engine: manual")
        return ManualTokenProvider(manual_token)
    if solver_name:
        return _make_solver(solver_name, api_key, sitekey)
    if env_bridge or bridge_port is not None:
        return _make_bridge(bridge_host, bridge_port, bridge_key, bridge_embed)
    try:
        return _make_playwright(headless, sitekey, page_url)
    except DependencyError:
        raise DependencyError(NO_ENGINE_GUIDANCE, code="dsc_turnstile_client")
