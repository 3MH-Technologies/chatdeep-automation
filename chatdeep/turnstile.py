"""Cloudflare Turnstile token providers.

Every chat message on chat-deep.ai must carry a **fresh, single-use**
Cloudflare Turnstile token (``ts`` field).  The browser obtains it from the
site's *invisible* widget (``execution: 'execute'``, sitekey published in the
page config).  Tokens stay valid ~300 s; the site refreshes at 240 s.

This module reproduces that flow faithfully:

* :class:`PlaywrightTokenProvider` drives a real Chromium browser to the
  chat page and runs **the site's own widget** with the site's own options —
  the same thing a normal visitor's browser does.  If Cloudflare ever shows
  an interactive challenge, run headed (``headless=False``) and solve it
  yourself, exactly like in a browser.
* :class:`ManualTokenProvider` accepts a token you copied from your own
  browser session (DevTools) — handy for one-off tests.

No challenge is bypassed or forged anywhere: this is standard browser
automation of the public page.
"""

from __future__ import annotations

import abc
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

from .errors import DependencyError, TurnstileClientError

log = logging.getLogger("chatdeep.turnstile")

TOKEN_TTL_S = 240.0          # site's TOKEN_TTL = 240000 ms
DEFAULT_PAGE = "https://chat-deep.ai/deepseek-chat/"
DEFAULT_SITEKEY = "0x4AAAAAAE8GhZK3uuXHsQvk"   # published in every chat page

# Renders the site's invisible widget and resolves with a single-use token.
# Mirrors Chat.prototype.runTurnstile (dsc-chat.js) option-for-option.
_RENDER_JS = r"""
async ([sitekey, timeoutMs]) => {
  const ready0 = Date.now();
  while (!(window.turnstile && typeof window.turnstile.render === 'function')) {
    if (Date.now() - ready0 > 20000) { throw new Error('turnstile-load'); }
    await new Promise(r => setTimeout(r, 100));
  }
  return await new Promise((resolve, reject) => {
    let done = false;
    const timer = setTimeout(() => finish(null, 'timeout'), timeoutMs);
    function finish(token, why) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      window.__cdpySettle = null;
      if (token) resolve(token);
      else reject(new Error(why || 'no-token'));
    }
    window.__cdpySettle = finish;
    let div = document.getElementById('cdpy-ts');
    if (!div) {
      div = document.createElement('div');
      div.id = 'cdpy-ts';
      // The site's own .cdc-ts container sits visibly in the page flow; an
      // off-screen container breaks the challenge iframe (error 300030), so
      // keep ours inside the viewport too (appearance:'interaction-only'
      // keeps it visually empty until interaction is actually required).
      div.style.cssText = 'position:fixed;bottom:12px;left:50%;transform:translateX(-50%);'
        + 'width:302px;min-height:2px;display:flex;justify-content:center;z-index:2147483647;';
      document.body.appendChild(div);
    }
    try {
      if (window.__cdpyWidget === undefined || window.__cdpyWidget === null) {
        window.__cdpyWidget = window.turnstile.render(div, {
          sitekey: sitekey,
          action: 'chat',
          execution: 'execute',
          appearance: 'interaction-only',
          'response-field': false,
          'refresh-expired': 'never',
          callback: (t) => { if (window.__cdpySettle) window.__cdpySettle(t); },
          'error-callback': (c) => { if (window.__cdpySettle) window.__cdpySettle(null, 'error ' + c); return true; },
          'timeout-callback': () => { if (window.__cdpySettle) window.__cdpySettle(null, 'interactive timeout'); },
          'expired-callback': () => { window.__cdpyTokenExpired = true; }
        });
      } else {
        window.turnstile.reset(window.__cdpyWidget);
      }
      window.turnstile.execute(window.__cdpyWidget);
    } catch (e) {
      finish(null, 'render ' + (e && e.message ? e.message : e));
    }
  });
}
"""


class TokenProvider(abc.ABC):
    """Supplies fresh single-use Turnstile tokens."""

    @abc.abstractmethod
    def take_token(self) -> str:
        """Return a fresh token (blocking).  Each token may be used once."""

    def prewarm(self) -> None:
        """Optionally start producing a token in the background."""

    def close(self) -> None:
        """Release any underlying resources."""

    # -- context manager sugar ------------------------------------------------
    def __enter__(self) -> "TokenProvider":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class ManualTokenProvider(TokenProvider):
    """Uses tokens you supply yourself (string, callable, or queue).

    Example: copy the ``ts`` value from a chat request in your browser's
    DevTools *before* it is sent, then feed it here.  Each token works once.
    """

    def __init__(self, source: Union[str, Callable[[], str]]):
        self._source = source
        self._used = False

    def take_token(self) -> str:
        if callable(self._source):
            tok = self._source()
        else:
            if self._used:
                raise TurnstileClientError(
                    "التوكن اليدوية صالحة لاستخدام واحد — زوّد توكن جديدة "
                    "(أو استخدم PlaywrightTokenProvider)",
                    code="dsc_turnstile_client",
                )
            tok, self._used = str(self._source), True
        if not tok or not tok.strip():
            raise TurnstileClientError("empty manual token", code="dsc_turnstile_client")
        return tok.strip()


class PlaywrightTokenProvider(TokenProvider):
    """Harvests tokens by running the site's own invisible widget in Chromium.

    A persistent browser profile (cookies, localStorage, fingerprint) is kept
    under ``profile_dir`` — the same continuity a regular browser has, which
    is what Cloudflare expects.  The provider is thread-safe and caches one
    token for up to :data:`TOKEN_TTL_S` seconds (pre-warm while you type).

    Args:
        sitekey:  Turnstile sitekey (auto-discovered from the page config by
                  :class:`~chatdeep.client.ChatDeepClient`; the default is the
                  currently published key).
        page_url: chat-deep.ai page used to host the widget (must be on the
                  site's origin).
        headless: run without a window.  If Cloudflare demands an interactive
                  challenge, set ``False`` and solve it once like a human.
        profile_dir: persistent context directory
                  (default ``~/.chatdeep/browser-profile``).
        widget_timeout_ms: per-run ceiling for the widget (default 60 s, same
                  as the site).
        user_agent / locale: browser identity overrides.
    """

    def __init__(self, sitekey: str = DEFAULT_SITEKEY, page_url: str = DEFAULT_PAGE,
                 *, headless: bool = True, profile_dir: Optional[Union[str, Path]] = None,
                 widget_timeout_ms: int = 60_000, user_agent: Optional[str] = None,
                 locale: str = "en-US", navigation_timeout_ms: int = 45_000,
                 attempts: int = 2, retry_delay_s: float = 2.0):
        self.sitekey = sitekey
        self.page_url = page_url
        self.headless = headless
        self.profile_dir = Path(profile_dir or (Path.home() / ".chatdeep" / "browser-profile"))
        self.widget_timeout_ms = widget_timeout_ms
        self.user_agent = user_agent
        self.locale = locale
        self.navigation_timeout_ms = navigation_timeout_ms
        self.attempts = max(1, attempts)
        self.retry_delay_s = retry_delay_s

        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._token_at: float = 0.0
        self._pw = None            # sync_playwright() handle
        self._ctx = None           # BrowserContext
        self._page = None          # Page
        self._started = False
        self._prewarm_thread: Optional[threading.Thread] = None

    # -- lifecycle -------------------------------------------------------------
    def _ensure_playwright(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise DependencyError(
                "Playwright غير مثبت. نفّذ:\n"
                "  pip install playwright\n"
                "  python -m playwright install chromium",
                code="dsc_turnstile_client",
            ) from exc
        return sync_playwright

    def start(self) -> None:
        """Launch the browser and open the chat page (idempotent)."""
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._started:
            return
        sync_playwright = self._ensure_playwright()
        log.debug("launching chromium (headless=%s, profile=%s)", self.headless, self.profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run"]
        launch_kw: dict = dict(
            headless=self.headless,
            args=args,
            locale=self.locale,
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 900},
        )
        # Headless: prefer the *new* headless mode (full Chrome binary via
        # channel="chromium") — a far more realistic fingerprint than the
        # legacy chrome-headless-shell, which Cloudflare rejects outright.
        if self.headless:
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), channel="chromium", **launch_kw)
            except Exception as exc:
                log.debug("channel=chromium launch failed (%s) — falling back", exc)
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), **launch_kw)
        else:
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), **launch_kw)
        self._ctx.set_default_navigation_timeout(self.navigation_timeout_ms)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        if self._page.url in ("about:blank", ""):
            self._page.goto(self.page_url, wait_until="domcontentloaded")
        self._started = True
        log.info("turnstile browser ready on %s", self._page.url)

    def close(self) -> None:
        with self._lock:
            try:
                if self._ctx is not None:
                    self._ctx.close()
                if self._pw is not None:
                    self._pw.stop()
            except Exception:  # pragma: no cover - best effort
                log.debug("error while closing playwright", exc_info=True)
            finally:
                self._ctx = self._page = self._pw = None
                self._started = False
                self._token, self._token_at = None, 0.0

    # -- token flow --------------------------------------------------------------
    def _run_widget_locked(self) -> str:
        assert self._page is not None
        # A stale page (network blip, crash) is recovered by reloading once.
        for attempt in (1, 2):
            try:
                tok = self._page.evaluate(_RENDER_JS, [self.sitekey, self.widget_timeout_ms])
                if tok and isinstance(tok, str):
                    return tok
                raise TurnstileClientError("widget returned empty token",
                                           code="dsc_turnstile_client")
            except TurnstileClientError:
                raise
            except Exception as exc:
                msg = str(exc)
                if attempt == 1 and ("Target page, context or browser has been closed" in msg
                                     or "Execution context was destroyed" in msg):
                    log.warning("page lost (%s) — reloading", msg)
                    try:
                        self._page.goto(self.page_url, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    continue
                raise TurnstileClientError(
                    f"فحص الحماية لم يكتمل: {msg}"
                    + (" — جرّب الوضع المرئي headless=False لحل التحدي تفاعليًا مرة واحدة"
                       if self.headless else "")
                    + " (إن كنت على IP تابع لمزود استضافة/سحابة فقد يرفضه Cloudflare"
                      " حتى من واجهة الموقع نفسها — استخدم اتصالًا عاديًا)",
                    code="dsc_turnstile_client",
                ) from exc
        raise TurnstileClientError("widget failed", code="dsc_turnstile_client")  # pragma: no cover

    def take_token(self) -> str:
        with self._lock:
            if self._token and (time.time() - self._token_at) < TOKEN_TTL_S:
                tok, self._token = self._token, None   # single use, like the site
                return tok
            self._token = None
            self._start_locked()
            last: Optional[Exception] = None
            for attempt in range(self.attempts):
                try:
                    tok = self._run_widget_locked()
                    log.debug("harvested turnstile token (%d chars, attempt %d)",
                              len(tok), attempt + 1)
                    return tok
                except TurnstileClientError as exc:
                    last = exc
                    if attempt + 1 < self.attempts:
                        log.info("turnstile attempt %d failed (%s) — retrying like the "
                                 "site's Retry button", attempt + 1, exc.message[:60])
                        time.sleep(self.retry_delay_s)
            assert last is not None
            raise last

    def prewarm(self) -> None:
        """Produce a token in the background (call while the user types)."""
        with self._lock:
            if self._token and (time.time() - self._token_at) < TOKEN_TTL_S:
                return
            if self._prewarm_thread and self._prewarm_thread.is_alive():
                return

        def _work():
            try:
                with self._lock:
                    self._start_locked()
                    tok = self._run_widget_locked()
                    self._token, self._token_at = tok, time.time()
            except Exception:
                log.debug("prewarm failed (will retry on send)", exc_info=True)

        self._prewarm_thread = threading.Thread(target=_work, daemon=True,
                                                name="cdpy-turnstile-prewarm")
        self._prewarm_thread.start()
