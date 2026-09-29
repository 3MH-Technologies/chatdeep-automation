"""High-level client for the chat-deep.ai internal API.

Flow implemented here (identical to the site's own dsc-chat.js):

1. ``POST admin-ajax.php?action=dsc_session`` (body ``v=2``) → nonce, quota,
   availability, consent state.  Sets the ``dsc_vid`` visitor cookie which
   carries the daily quota identity.
2. If consent is required (EEA/UK/CH): ``POST admin-ajax.php?action=dsc_notice``
   (``ok=1`` + ``X-WP-Nonce``).
3. Per message: obtain a fresh single-use Cloudflare Turnstile token, then
   ``POST /wp-json/dsc/v2/chat`` with
   ``{"messages": [...], "mode": "fast"|"pro", "helpdesk": bool,
      "ts": <token>, "hp": ""}`` and header ``X-WP-Nonce``.
   The response is an SSE stream (``reasoning`` / ``delta`` / ``done`` /
   ``error``) or a JSON error body.

Full endpoint documentation (Arabic): ``docs/API.md``.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Union

import requests

from .errors import (
    ChatDeepError,
    ConsentRequired,
    HttpError,
    NonceError,
    QuotaExceeded,
    RateLimited,
    ServicePaused,
    TurnstileError,
    ValidationError,
    error_from_payload,
)
from .images import prepare_images
from .models import (
    EVENT_DELTA,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_REASONING,
    ChatAnswer,
    ChatMessage,
    CompareResult,
    Quota,
    SessionInfo,
    SiteConfig,
    StreamEvent,
)
from .providers import build_token_provider
from .sse import iter_events
from .turnstile import DEFAULT_PAGE, DEFAULT_SITEKEY, TokenProvider

log = logging.getLogger("chatdeep.client")

BASE_URL = "https://chat-deep.ai"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Hard-coded fallbacks (verified 2026-09); discover_config() prefers live values.
FALLBACK_CONFIG = SiteConfig(
    session_url=f"{BASE_URL}/wp-admin/admin-ajax.php?action=dsc_session",
    chat_url=f"{BASE_URL}/wp-json/dsc/v2/chat",
    notice_url=f"{BASE_URL}/wp-admin/admin-ajax.php?action=dsc_notice",
    turnstile_sitekey=DEFAULT_SITEKEY,
    full_page_url=DEFAULT_PAGE,
)

STATUS_URL = f"{BASE_URL}/wp-content/plugins/chatdeep-site-kit/status.php"
REGION_URL = f"{BASE_URL}/wp-content/plugins/chatdeep-site-kit/region.php"

_CDC_RE = re.compile(r'data-cdc="([^"]+)"')


# ---------------------------------------------------------------------------
# Config discovery
# ---------------------------------------------------------------------------

def discover_config(page_url: str = f"{BASE_URL}/", *, timeout: int = 30,
                    user_agent: str = DEFAULT_UA) -> SiteConfig:
    """Scrape the ``data-cdc`` widget config from a chat page.

    The site embeds endpoint URLs, the Turnstile sitekey and every limit in a
    JSON blob on the chat element.  Reading it live keeps the tool aligned
    with server-side changes (plugin updates, quota changes, key rotation).
    Falls back to :data:`FALLBACK_CONFIG` when the page cannot be parsed.
    """
    try:
        r = requests.get(page_url, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        m = _CDC_RE.search(r.text)
        if not m:
            raise ValueError("data-cdc attribute not found")
        cdc = json.loads(html_mod.unescape(m.group(1)))
        cfg = SiteConfig.from_cdc(cdc)
        if not (cfg.session_url and cfg.chat_url and cfg.turnstile_sitekey):
            raise ValueError("incomplete data-cdc payload")
        log.debug("discovered live config: %s", cfg.raw)
        return cfg
    except Exception as exc:
        log.warning("config discovery failed (%s) — using built-in defaults", exc)
        return FALLBACK_CONFIG


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

OnDelta = Callable[[str], None]
OnReasoning = Callable[[], None]
OnDone = Callable[[Dict[str, Any]], None]


class ChatDeepClient:
    """Synchronous client for the Chat-Deep.ai browser chat.

    Example::

        with ChatDeepClient() as c:
            for ev in c.chat_events("اشرح لي الذكاء الاصطناعي"):
                if ev.kind == "delta":
                    print(ev.text, end="", flush=True)

    Args:
        base_url: site origin.
        token_provider: how Turnstile tokens are produced.  Defaults to a
            headless :class:`~chatdeep.turnstile.PlaywrightTokenProvider`.
        headless: passed to the default provider.
        cookie_store: JSON file persisting the ``dsc_vid`` visitor cookie so
            your daily quota identity survives restarts (``None`` disables).
        auto_consent: automatically accept the regional notice when required.
        fallback_to_fast: on ``dsc_pro_limit``, silently use Fast mode.
        min_interval: minimum seconds between two chat sends (politeness and
            ``dsc_rate_limit`` avoidance).
        request_timeout: ``(connect, read)`` timeout; read applies between
            stream chunks.
        discover: fetch the live ``data-cdc`` config on first use.
    """

    def __init__(self, base_url: str = BASE_URL, *,
                 token_provider: Optional[TokenProvider] = None,
                 engine: Optional[str] = None,
                 solver: Optional[str] = None,
                 api_key: Optional[str] = None,
                 manual_token: Optional[str] = None,
                 bridge_port: Optional[int] = None,
                 bridge_key: Optional[str] = None,
                 bridge_embed: Optional[bool] = None,
                 headless: bool = True,
                 cookie_store: Optional[Union[str, Path]] = Path.home() / ".chatdeep" / "cookies.json",
                 auto_consent: bool = True,
                 fallback_to_fast: bool = True,
                 min_interval: float = 1.5,
                 request_timeout=(10, 600),
                 user_agent: str = DEFAULT_UA,
                 discover: bool = True,
                 system_prompt: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.auto_consent = auto_consent
        self.fallback_to_fast = fallback_to_fast
        self.min_interval = min_interval
        self.request_timeout = request_timeout
        self.system_prompt = system_prompt
        self._headless = headless

        # Engine configuration (used when no explicit token_provider is given)
        self.engine = engine
        self.solver = solver
        self.api_key = api_key
        self.manual_token = manual_token
        self.bridge_port = bridge_port
        self.bridge_key = bridge_key
        self.bridge_embed = bridge_embed

        self._cfg: Optional[SiteConfig] = None
        self._discover = discover

        self._http = requests.Session()
        self._http.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        })

        self._cookie_store = Path(cookie_store).expanduser() if cookie_store else None
        self._load_cookies()

        self._explicit_provider = token_provider
        self._provider = token_provider
        self._provider_lock = threading.Lock()

        self.session_info: Optional[SessionInfo] = None
        self.quota: Optional[Quota] = None
        self.region: Optional[str] = None
        self.history: List[ChatMessage] = []
        self._last_send = 0.0
        self._send_lock = threading.Lock()

    # -- configuration -------------------------------------------------------
    @property
    def cfg(self) -> SiteConfig:
        if self._cfg is None:
            self._cfg = discover_config(self.base_url + "/") if self._discover else FALLBACK_CONFIG
        return self._cfg

    @property
    def provider(self) -> TokenProvider:
        with self._provider_lock:
            if self._provider is None:
                self._provider = build_token_provider(
                    engine=self.engine or "auto",
                    solver=self.solver,
                    api_key=self.api_key,
                    token=self.manual_token,
                    bridge_port=self.bridge_port,
                    bridge_key=self.bridge_key,
                    bridge_embed=self.bridge_embed,
                    headless=self._headless,
                    sitekey=self.cfg.turnstile_sitekey,
                    page_url=self.cfg.full_page_url or DEFAULT_PAGE,
                )
            return self._provider

    def set_headless(self, headless: bool) -> None:
        """(Re)configure window mode (only meaningful for the playwright engine)."""
        with self._provider_lock:
            self._headless = headless
            if self._explicit_provider is None and self._provider is not None:
                self._provider.close()
                self._provider = None

    # -- cookies ---------------------------------------------------------------
    def _load_cookies(self) -> None:
        if not self._cookie_store or not self._cookie_store.exists():
            return
        try:
            data = json.loads(self._cookie_store.read_text(encoding="utf-8"))
            for name, value in (data.get("cookies") or {}).items():
                self._http.cookies.set_cookie(
                    requests.cookies.create_cookie(name, value, domain=".chat-deep.ai"))
            log.debug("loaded cookies: %s", list(data.get("cookies") or {}))
        except Exception:
            log.warning("could not read cookie store %s", self._cookie_store, exc_info=True)

    def _save_cookies(self) -> None:
        if not self._cookie_store:
            return
        try:
            self._cookie_store.parent.mkdir(parents=True, exist_ok=True)
            payload = {"saved_at": time.time(),
                       "cookies": {c.name: c.value for c in self._http.cookies}}
            self._cookie_store.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except Exception:  # pragma: no cover
            log.warning("could not write cookie store", exc_info=True)

    # -- bootstrap endpoints -----------------------------------------------------
    def fetch_region(self) -> str:
        """``GET region.php`` → ``eea`` | ``uk`` | ``ch`` | ``row``."""
        try:
            r = self._http.get(REGION_URL, timeout=self.request_timeout[0] + 10)
            self.region = r.json().get("region", "row")
        except Exception:
            self.region = "row"
        return self.region or "row"

    def bootstrap(self, force: bool = False) -> SessionInfo:
        """Create/refresh the chat session (``dsc_session``) and handle consent.

        Called automatically before the first message; ``force=True`` rotates
        the nonce (after ``dsc_bad_nonce``).
        """
        if self.session_info and not force:
            return self.session_info
        url = self.cfg.session_url
        try:
            r = self._http.post(url, data="v=2", stream=False, timeout=self.request_timeout,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
            if r.status_code != 200:
                raise HttpError(f"dsc_session HTTP {r.status_code}", status=r.status_code)
            info = SessionInfo.from_dict(r.json())
        except requests.RequestException as exc:
            raise HttpError(f"تعذّر الوصول إلى dsc_session: {exc}", code="dsc_session") from exc

        self.session_info = info
        self.quota = info.quota
        self._save_cookies()

        if info.paused:
            log.warning("service paused (monthly cap)")
        if not info.access_chat:
            log.warning("chat access denied by session payload")
        if info.consent.required and not info.consent.given:
            if not self.auto_consent:
                raise ConsentRequired("مطلوب قبول الإشعار الإقليمي أولًا (auto_consent=False)")
            self.accept_notice()
        log.info("session ready (nonce=%s…, quota=%s)", info.nonce[:6], info.quota)
        return info

    def accept_notice(self) -> bool:
        """``POST dsc_notice`` (``ok=1``) — accept the regional privacy notice."""
        info = self.bootstrap() if not self.session_info else self.session_info
        r = self._http.post(self.cfg.notice_url, data="ok=1", timeout=self.request_timeout,
                            headers={"Content-Type": "application/x-www-form-urlencoded",
                                     "X-WP-Nonce": info.nonce})
        if r.status_code != 200:
            raise HttpError(f"dsc_notice HTTP {r.status_code}", status=r.status_code)
        given = bool(r.json().get("given"))
        if given and self.session_info:
            self.session_info.consent.given = True
        return given

    def service_status(self) -> Dict[str, Any]:
        """``GET status.php`` — the site's independent DeepSeek monitoring."""
        try:
            r = self._http.get(STATUS_URL, timeout=self.request_timeout[0] + 10,
                               headers={"Cache-Control": "no-store"})
            return r.json()
        except Exception as exc:
            raise HttpError(f"تعذّر جلب الحالة: {exc}") from exc

    # -- payload building --------------------------------------------------------
    def build_payload_messages(self, history: Sequence[ChatMessage],
                               new_user: Optional[ChatMessage] = None) -> List[Dict[str, Any]]:
        """Apply the site's context rules (last N, images on last user msg)."""
        msgs = [m for m in history if m.role in ("user", "assistant") and not m.error]
        if new_user is not None:
            msgs = msgs + [new_user]
        msgs = msgs[-self.cfg.context:]
        last_image_idx = -1
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].role == "user" and msgs[i].images:
                last_image_idx = i
                break
        return [m.to_payload(include_images=(i == last_image_idx)) for i, m in enumerate(msgs)]

    def _validate(self, user_msg: ChatMessage) -> None:
        cfg = self.cfg
        if len(user_msg.content) > cfg.max_chars:
            raise ValidationError(
                f"الرسالة {len(user_msg.content)} حرفًا — الحد {cfg.max_chars}",
                code="dsc_too_long")
        if len(user_msg.images) > cfg.max_images:
            raise ValidationError(
                f"{len(user_msg.images)} صور — الحد {cfg.max_images}",
                code="dsc_too_many_images")

    def _quota_guard(self, mode: str, legs: int = 1) -> str:
        """Client-side quota check; returns the effective mode."""
        q = self.quota
        if q is None or q.unlimited:
            return mode
        if q.day_left < legs:
            raise QuotaExceeded(
                f"انتهى الحد اليومي ({q.day_used}/{q.day_limit}) — يعاد الضبط {q.resets_at}",
                code="dsc_day_limit", data={"quota": q.__dict__})
        if mode == "pro" and q.pro_left < legs:
            if self.fallback_to_fast and q.day_left >= legs:
                log.warning("pro quota exhausted — falling back to fast")
                return "fast"
            raise QuotaExceeded(
                f"انتهى حد Pro اليومي ({q.pro_used}/{q.pro_limit})",
                code="dsc_pro_limit", data={"quota": q.__dict__})
        return mode

    def _throttle(self) -> None:
        with self._send_lock:
            wait = self.min_interval - (time.time() - self._last_send)
            if wait > 0:
                time.sleep(wait)
            self._last_send = time.time()

    def prepare_images_arg(self, images: Optional[Iterable[Any]]) -> List[str]:
        """Normalise an ``images`` argument into data URLs.

        Already-encoded ``data:`` URLs pass through untouched; file paths,
        raw bytes and file objects go through the site-equivalent canvas
        pipeline (:mod:`chatdeep.images`).
        """
        items = list(images or [])
        urls = [i for i in items if isinstance(i, str) and i.startswith("data:")]
        files = [i for i in items if not (isinstance(i, str) and i.startswith("data:"))]
        return urls + prepare_images(files, max_images=self.cfg.max_images,
                                     max_bytes=self.cfg.max_image_bytes)

    # -- core chat -----------------------------------------------------------------
    def chat_events(self, prompt: Optional[str] = None, *,
                    messages: Optional[Sequence[ChatMessage]] = None,
                    mode: str = "fast",
                    images: Optional[Sequence[Any]] = None,
                    helpdesk: bool = False,
                    use_history: bool = True) -> Iterator[StreamEvent]:
        """Low-level generator: yields every SSE event of one answer.

        Provide either ``prompt`` (appended to ``self.history`` when
        ``use_history``) or an explicit ``messages`` list.  ``images`` accepts
        file paths / bytes / ready data URLs.
        """
        if mode not in ("fast", "pro"):
            raise ValidationError(f"وضع غير معروف: {mode} (المسموح: fast, pro)")
        if helpdesk:
            mode = "fast"   # site forces fast in helpdesk mode

        if messages is not None:
            payload_msgs = [m.to_payload(include_images=(i == len(messages) - 1))
                            for i, m in enumerate(messages)]
        else:
            prepared = self.prepare_images_arg(images)
            user_msg = ChatMessage.user(prompt or "", images=prepared)
            self._validate(user_msg)
            mode = self._quota_guard(mode)
            base = self.history if use_history else []
            payload_msgs = self.build_payload_messages(base, user_msg)
            if self.system_prompt and not base:
                payload_msgs.insert(0, {"role": "user", "content": self.system_prompt})
                payload_msgs.insert(1, {"role": "assistant", "content": "Understood."})

        if not any(m.get("content") or m.get("images") for m in payload_msgs if m["role"] == "user"):
            raise ValidationError("لا يوجد نص أو صورة في الرسالة", code="dsc_too_long")

        info = self.bootstrap()
        if info.paused:
            raise ServicePaused("الشات المجاني موقوف لهذا الشهر")
        if not info.access_chat:
            raise ChatDeepError("الوصول إلى الشات غير مسموح حاليًا", code="dsc_session")

        self._throttle()
        retried_nonce = retried_ts = False

        while True:
            token = self.provider.take_token()
            body = {"messages": payload_msgs, "mode": mode, "helpdesk": bool(helpdesk),
                    "ts": token, "hp": ""}     # hp = honeypot input, must stay empty
            headers = {"Content-Type": "application/json", "X-WP-Nonce": info.nonce,
                       "Accept": "text/event-stream"}
            try:
                resp = self._http.post(self.cfg.chat_url, data=json.dumps(body),
                                       headers=headers, stream=True,
                                       timeout=self.request_timeout)
            except requests.RequestException as exc:
                raise HttpError(f"فشل الاتصال بنقطة الشات: {exc}") from exc

            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code != 200 or "text/event-stream" not in ctype:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {"code": "dsc_http",
                               "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                resp.close()
                err = error_from_payload(payload, status=resp.status_code)
                if payload.get("data", {}).get("quota"):
                    self.quota = Quota.from_dict(payload["data"]["quota"])
                # Same silent single-retry policy as the site's client.
                if isinstance(err, NonceError) and not retried_nonce:
                    retried_nonce = True
                    log.info("bad nonce — refreshing session and retrying once")
                    info = self.bootstrap(force=True)
                    continue
                if isinstance(err, TurnstileError) and resp.status_code == 403 and not retried_ts:
                    retried_ts = True
                    log.info("turnstile rejected — harvesting a fresh token and retrying once")
                    continue
                raise err

            # --- stream the answer ------------------------------------------------
            try:
                for ev in iter_events(resp.iter_content(chunk_size=1024)):
                    if ev.kind == EVENT_DONE or ev.kind == EVENT_ERROR:
                        q = ev.data.get("quota")
                        if q:
                            self.quota = Quota.from_dict(q)
                        if ev.data.get("paused"):
                            if self.session_info:
                                self.session_info.paused = True
                    yield ev
                    if ev.kind == EVENT_ERROR:
                        raise error_from_payload(ev.data)
            except requests.RequestException as exc:
                raise HttpError(f"انقطع البث: {exc}") from exc
            finally:
                resp.close()
                self._save_cookies()
            return

    def chat(self, prompt: Optional[str] = None, *, mode: str = "fast",
             images: Optional[Sequence[Any]] = None, helpdesk: bool = False,
             messages: Optional[Sequence[ChatMessage]] = None,
             use_history: bool = True, keep: bool = True,
             on_reasoning: Optional[OnReasoning] = None,
             on_delta: Optional[OnDelta] = None,
             on_done: Optional[OnDone] = None) -> ChatAnswer:
        """Send one message and consume the whole stream into a ChatAnswer.

        ``on_delta(text)`` fires per streamed chunk — wire it to ``print`` or
        a UI for live output.  ``keep=True`` appends the exchange to
        ``self.history`` so follow-up calls share context (last 20 messages,
        like the website).  If the stream dies mid-answer, the partial text
        is kept and returned with ``incomplete=True`` (site behaviour);
        errors with no content at all are raised.
        """
        prepared: List[str] = []
        if messages is None and prompt is not None:
            prepared = self.prepare_images_arg(images)

        t0 = time.time()
        answer = ChatAnswer(mode="fast" if helpdesk else mode, helpdesk=helpdesk)
        reason_start: Optional[float] = None

        try:
            for ev in self.chat_events(prompt, messages=messages, mode=mode,
                                       images=prepared or None,
                                       helpdesk=helpdesk, use_history=use_history):
                if ev.kind == EVENT_REASONING:
                    reason_start = time.time()
                    answer.started_reasoning_at = reason_start
                    if on_reasoning:
                        on_reasoning()
                elif ev.kind == EVENT_DELTA:
                    if reason_start and answer.reasoned_ms is None:
                        answer.reasoned_ms = int((time.time() - reason_start) * 1000)
                    answer.content += ev.text
                    if on_delta:
                        on_delta(ev.text)
                elif ev.kind == EVENT_DONE:
                    answer.finish = str(ev.data.get("finish", "stop"))
                    if ev.data.get("reasoned_ms"):
                        answer.reasoned_ms = int(ev.data["reasoned_ms"])
                    answer.quota = Quota.from_dict(ev.data.get("quota"))
                    if answer.finish in ("length", "interrupted"):
                        answer.incomplete = True
                    if on_done:
                        on_done(ev.data)
                elif ev.kind == EVENT_ERROR:
                    answer.finish = "error"
        except ChatDeepError:
            answer.total_ms = int((time.time() - t0) * 1000)
            if keep and messages is None and prompt is not None:
                # The site keeps the user turn and flags the failed answer so
                # it is excluded from future context.
                self.history.append(ChatMessage.user(prompt, images=prepared))
                self.history.append(ChatMessage(role="assistant", content=answer.content,
                                                mode=answer.mode, error=True))
            if answer.content:
                answer.incomplete = True
                log.warning("stream failed mid-answer — returning partial content")
                return answer
            raise

        answer.total_ms = int((time.time() - t0) * 1000)
        if keep and messages is None and prompt is not None:
            self.history.append(ChatMessage.user(prompt, images=prepared))
            self.history.append(answer.to_message())
        return answer

    def ask(self, prompt: str, *, mode: str = "fast",
            images: Optional[Sequence[Any]] = None, **kw: Any) -> str:
        """Convenience: one prompt in, answer text out."""
        return self.chat(prompt, mode=mode, images=images, **kw).content

    # -- compare ---------------------------------------------------------------------
    def compare(self, prompt: str, *, images: Optional[Sequence[Any]] = None,
                on_delta: Optional[Callable[[str, str], None]] = None) -> CompareResult:
        """Send one prompt to Fast and Pro in parallel (costs 2 messages).

        ``on_delta(mode, text)`` streams both columns live.
        """
        q = self.quota
        if q and not q.unlimited:
            if q.day_left < 2:
                raise QuotaExceeded("المقارنة تتطلب رسالتين متبقيتين على الأقل",
                                    code="dsc_day_limit")
            if q.pro_left < 1:
                raise QuotaExceeded("لا توجد حصة Pro متبقية للمقارنة", code="dsc_pro_limit")

        # Build one shared payload; images are prepared once.
        prepared = self.prepare_images_arg(images)
        msgs = self.build_payload_messages(self.history, ChatMessage.user(prompt, prepared))
        result = CompareResult(prompt=prompt)

        def _leg(mode: str) -> ChatAnswer:
            ans = ChatAnswer(mode=mode)
            t0 = time.time()
            for ev in self.chat_events(messages=[ChatMessage(m["role"], m.get("content", ""),
                                                             m.get("images", []))
                                                 for m in msgs], mode=mode, use_history=False):
                if ev.kind == EVENT_REASONING:
                    pass
                elif ev.kind == EVENT_DELTA:
                    ans.content += ev.text
                    if on_delta:
                        on_delta(mode, ev.text)
                elif ev.kind == EVENT_DONE:
                    ans.finish = str(ev.data.get("finish", "stop"))
                    if ev.data.get("reasoned_ms"):
                        ans.reasoned_ms = int(ev.data["reasoned_ms"])
            ans.total_ms = int((time.time() - t0) * 1000)
            return ans

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="cdpy-cmp") as pool:
            f_fast = pool.submit(_leg, "fast")
            f_pro = pool.submit(_leg, "pro")
            for fut, key in ((f_fast, "fast"), (f_pro, "pro")):
                try:
                    setattr(result, key, fut.result())
                except ChatDeepError as exc:
                    setattr(result, f"{key}_error", f"{exc.code}: {exc.message}")
        if result.fast:
            self.history.append(ChatMessage.user(prompt, prepared))
            self.history.append(result.fast.to_message())
        return result

    # -- housekeeping -------------------------------------------------------------------
    def prewarm(self) -> None:
        """Start browser + token harvest in the background (call early)."""
        try:
            self.provider.prewarm()
        except ChatDeepError:
            log.debug("prewarm unavailable", exc_info=True)

    def reset_history(self) -> None:
        self.history.clear()

    def close(self) -> None:
        self._save_cookies()
        if self._provider is not None:
            self._provider.close()
        self._http.close()

    def __enter__(self) -> "ChatDeepClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
