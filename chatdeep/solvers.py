"""Token providers based on commercial CAPTCHA-solving APIs (no local browser).

Supported services (all expose the same createTask/getTaskResult protocol):

===========  ==============================  =============================
Provider     Endpoint                        Task type
===========  ==============================  =============================
CapSolver    https://api.capsolver.com       AntiTurnstileTaskProxyLess
2Captcha     https://api.2captcha.com        TurnstileTaskProxyless
YesCaptcha   https://api.yescaptcha.com      AntiTurnstileTaskProxyLess
===========  ==============================  =============================

You need an API key from the service (paid per ~1000 solves).  The provider
creates a Turnstile task for chat-deep.ai's sitekey and polls until the
service returns a token, which is then used exactly like a browser-harvested
one in the ``ts`` field of the chat request.

Compliance note: using a solving service may conflict with Cloudflare's or
the site's terms of service — the free "bridge" provider
(:mod:`chatdeep.bridge`) keeps everything inside your own browser instead.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

import requests

from .errors import TurnstileClientError
from .turnstile import DEFAULT_SITEKEY, TokenProvider

log = logging.getLogger("chatdeep.solvers")

CHAT_PAGE_URL = "https://chat-deep.ai/"


class TaskSolverProvider(TokenProvider):
    """Generic createTask/getTaskResult solver client.

    Args:
        api_key: your service API key.
        website_url: page URL registered with the sitekey.
        website_key: Turnstile sitekey (default: chat-deep.ai's published key).
        timeout: overall ceiling for one solve (seconds).
        poll_interval: seconds between getTaskResult polls.
        action: optional Turnstile ``action`` metadata (chat-deep.ai's widget
                uses ``chat``; CapSolver/YesCaptcha accept it via metadata).
    """

    name = "generic"
    api_base = ""
    task_type = ""
    _supports_metadata = False

    def __init__(self, api_key: str, *, website_url: str = CHAT_PAGE_URL,
                 website_key: str = DEFAULT_SITEKEY, timeout: float = 120.0,
                 poll_interval: float = 3.0, action: Optional[str] = "chat",
                 http: Optional[requests.Session] = None):
        if not api_key:
            raise TurnstileClientError(
                f"مفتاح API غير مضبوط لخدمة {self.name}", code="dsc_turnstile_client")
        self.api_key = api_key
        self.website_url = website_url
        self.website_key = website_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.action = action
        self._http = http or requests.Session()
        self._lock = threading.Lock()
        self.last_task_id: Optional[str] = None
        self.last_solve_ms: Optional[int] = None

    # -- protocol ----------------------------------------------------------------
    def _task_payload(self) -> Dict[str, Any]:
        task: Dict[str, Any] = {
            "type": self.task_type,
            "websiteURL": self.website_url,
            "websiteKey": self.website_key,
        }
        if self.action and self._supports_metadata:
            task["metadata"] = {"action": self.action}
        return task

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            r = self._http.post(self.api_base + path, json=payload, timeout=30)
        except requests.RequestException as exc:
            raise TurnstileClientError(
                f"تعذّر الاتصال بـ {self.name}: {exc}", code="dsc_turnstile_client") from exc
        try:
            return r.json()
        except ValueError as exc:
            raise TurnstileClientError(
                f"استجابة غير JSON من {self.name} (HTTP {r.status_code}): {r.text[:200]}",
                code="dsc_turnstile_client") from exc

    def _create_task(self) -> str:
        resp = self._post("/createTask", {"clientKey": self.api_key,
                                          "task": self._task_payload()})
        if resp.get("errorId"):
            raise TurnstileClientError(
                f"{self.name} createTask فشل: [{resp.get('errorCode')}] "
                f"{resp.get('errorDescription')}", code="dsc_turnstile_client",
                data=resp)
        task_id = resp.get("taskId")
        if not task_id:
            raise TurnstileClientError(
                f"{self.name}: لا taskId في الاستجابة: {resp}", code="dsc_turnstile_client")
        return str(task_id)

    def _get_result(self, task_id: str) -> Dict[str, Any]:
        resp = self._post("/getTaskResult", {"clientKey": self.api_key, "taskId": task_id})
        if resp.get("errorId"):
            raise TurnstileClientError(
                f"{self.name} getTaskResult فشل: [{resp.get('errorCode')}] "
                f"{resp.get('errorDescription')}", code="dsc_turnstile_client", data=resp)
        return resp

    # -- TokenProvider ---------------------------------------------------------------
    def take_token(self) -> str:
        with self._lock:
            t0 = time.time()
            task_id = self._create_task()
            self.last_task_id = task_id
            log.info("%s task %s created — polling…", self.name, task_id[:12])
            while True:
                time.sleep(self.poll_interval)
                resp = self._get_result(task_id)
                status = resp.get("status")
                if status == "ready":
                    token = (resp.get("solution") or {}).get("token")
                    if not token:
                        raise TurnstileClientError(
                            f"{self.name}: solution بلا توكن: {resp}",
                            code="dsc_turnstile_client")
                    self.last_solve_ms = int((time.time() - t0) * 1000)
                    log.info("%s solved in %.1fs", self.name, self.last_solve_ms / 1000)
                    return token
                if status == "failed":
                    raise TurnstileClientError(
                        f"{self.name}: المهمة فشلت: [{resp.get('errorCode')}] "
                        f"{resp.get('errorDescription')}", code="dsc_turnstile_client",
                        data=resp)
                if time.time() - t0 > self.timeout:
                    raise TurnstileClientError(
                        f"{self.name}: انتهت مهلة الحل ({self.timeout:.0f}s)",
                        code="dsc_turnstile_client")

    def get_balance(self) -> Optional[float]:
        """Query the solver account balance (diagnostics; free call)."""
        resp = self._post("/getBalance", {"clientKey": self.api_key})
        if resp.get("errorId"):
            raise TurnstileClientError(
                f"{self.name} getBalance فشل: [{resp.get('errorCode')}] "
                f"{resp.get('errorDescription')}", code="dsc_turnstile_client", data=resp)
        bal = resp.get("balance")
        return float(bal) if bal is not None else None

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # pragma: no cover
            pass


class CapSolverProvider(TaskSolverProvider):
    """CapSolver — https://www.capsolver.com (``CAPSOLVER_API_KEY``)."""

    name = "capsolver"
    api_base = "https://api.capsolver.com"
    task_type = "AntiTurnstileTaskProxyLess"
    _supports_metadata = True


class TwoCaptchaProvider(TaskSolverProvider):
    """2Captcha — https://2captcha.com (``TWOCAPTCHA_API_KEY``)."""

    name = "2captcha"
    api_base = "https://api.2captcha.com"
    task_type = "TurnstileTaskProxyless"
    _supports_metadata = False   # uses top-level `action` only for challenge pages


class YesCaptchaProvider(TaskSolverProvider):
    """YesCaptcha — https://yescaptcha.com (``YESCAPTCHA_API_KEY``)."""

    name = "yescaptcha"
    api_base = "https://api.yescaptcha.com"
    task_type = "AntiTurnstileTaskProxyLess"
    _supports_metadata = True


SOLVERS: Dict[str, type] = {
    "capsolver": CapSolverProvider,
    "2captcha": TwoCaptchaProvider,
    "yescaptcha": YesCaptchaProvider,
}


def make_solver_provider(service: str, api_key: str, **kw: Any) -> TaskSolverProvider:
    """Factory: ``make_solver_provider("capsolver", key)``."""
    service = service.strip().lower().replace("-", "").replace("_", "")
    if service in ("twocaptcha", "2cap"):
        service = "2captcha"
    cls = SOLVERS.get(service)
    if cls is None:
        raise TurnstileClientError(
            f"خدمة حل غير معروفة: {service} (المدعوم: {', '.join(SOLVERS)})",
            code="dsc_turnstile_client")
    return cls(api_key, **kw)
