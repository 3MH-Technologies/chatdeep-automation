"""Exception hierarchy mirroring the server/client error codes of chat-deep.ai.

The site reports failures either as a JSON body (HTTP != 200) or as an SSE
``error`` event.  Both carry ``{"code": "dsc_...", "message": "...", "data":
{...}}``.  This module maps every known code onto a typed exception so
callers can ``except`` precisely, and keeps a human-readable table for the
CLI and the docs.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ChatDeepError(Exception):
    """Base class for every error raised by this client."""

    code: str = "dsc_client"

    def __init__(self, message: str = "", *, code: Optional[str] = None,
                 status: Optional[int] = None, data: Optional[Dict[str, Any]] = None):
        super().__init__(message or self.code)
        if code:
            self.code = code
        self.message = message or self.code
        self.status = status
        self.data = data or {}

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} code={self.code!r} status={self.status} msg={self.message!r}>"


class SessionError(ChatDeepError):
    """``dsc_session`` — the session bootstrap itself failed."""

    code = "dsc_session"


class NonceError(ChatDeepError):
    """``dsc_bad_nonce`` — stale ``X-WP-Nonce``; refresh the session and retry."""

    code = "dsc_bad_nonce"


class TurnstileError(ChatDeepError):
    """``dsc_turnstile`` — the server rejected the Cloudflare Turnstile token."""

    code = "dsc_turnstile"


class TurnstileClientError(ChatDeepError):
    """``dsc_turnstile_client`` — the token could not be produced locally
    (widget failed / timed out / provider missing)."""

    code = "dsc_turnstile_client"


class ConsentRequired(ChatDeepError):
    """``dsc_consent`` — the regional notice (EEA/UK/CH) must be accepted
    via ``dsc_notice`` before chatting."""

    code = "dsc_consent"


class QuotaExceeded(ChatDeepError):
    """``dsc_day_limit`` / ``dsc_pro_limit`` — daily message cap reached."""

    code = "dsc_day_limit"


class RateLimited(ChatDeepError):
    """``dsc_rate_limit`` — sending too fast; back off and retry."""

    code = "dsc_rate_limit"


class ServicePaused(ChatDeepError):
    """``dsc_paused`` — the free chat hit its monthly cap (reopens on the 1st)."""

    code = "dsc_paused"


class ValidationError(ChatDeepError):
    """``dsc_too_long`` / ``dsc_bad_image`` / ``dsc_too_many_images`` — the
    request violates a documented client-side limit."""

    code = "dsc_too_long"


class UpstreamError(ChatDeepError):
    """``dsc_upstream`` — DeepSeek's API (the site's backend) failed."""

    code = "dsc_upstream"


class HttpError(ChatDeepError):
    """``dsc_http`` or any transport-level failure (timeout, DNS, TLS...)."""

    code = "dsc_http"


class DependencyError(ChatDeepError):
    """A required optional dependency (e.g. Playwright) is missing."""

    code = "dsc_dependency"


#: code -> exception class.  Unknown ``dsc_*`` codes fall back to
#: :class:`ChatDeepError` so new server codes never crash the client.
ERROR_CLASSES: Dict[str, type] = {
    "dsc_session": SessionError,
    "dsc_bad_nonce": NonceError,
    "dsc_turnstile": TurnstileError,
    "dsc_turnstile_client": TurnstileClientError,
    "dsc_consent": ConsentRequired,
    "dsc_day_limit": QuotaExceeded,
    "dsc_pro_limit": QuotaExceeded,
    "dsc_rate_limit": RateLimited,
    "dsc_paused": ServicePaused,
    "dsc_too_long": ValidationError,
    "dsc_bad_image": ValidationError,
    "dsc_too_many_images": ValidationError,
    "dsc_upstream": UpstreamError,
    "dsc_http": HttpError,
}

#: Friendly description per code (shown by the CLI, also used in docs/API.md).
ERROR_INFO: Dict[str, str] = {
    "dsc_session": "تعذّر إنشاء جلسة — تحقق من الاتصال ثم أعد المحاولة.",
    "dsc_bad_nonce": "الـ nonce منتهي الصلاحية — يُحدَّث تلقائيًا وتُعاد المحاولة مرة واحدة.",
    "dsc_turnstile": "فشل فحص الحماية (Turnstile) — تُطلب توكن جديدة وتُعاد المحاولة مرة واحدة.",
    "dsc_turnstile_client": "تعذّر توليد توكن الحماية محليًا (المتصفح/الأداة غير متاحة).",
    "dsc_consent": "مطلوب قبول الإشعار الإقليمي (EEA/UK/CH) عبر نقطة dsc_notice أولًا.",
    "dsc_day_limit": "انتهى الحد اليومي للرسائل (50 رسالة/يوم).",
    "dsc_pro_limit": "انتهى حد وضع Pro اليومي (10 رسائل/يوم) — يمكن التحويل إلى Fast.",
    "dsc_rate_limit": "إرسال سريع جدًا — انتظر قليلًا ثم أعد المحاولة.",
    "dsc_paused": "الشات المجاني موقوف لهذا الشهر (يعود في اليوم الأول من الشهر).",
    "dsc_too_long": "الرسالة أطول من الحد المسموح (6000 حرف).",
    "dsc_bad_image": "صورة غير صالحة أو أكبر من 5 ميغابايت أو بصيغة غير مدعومة.",
    "dsc_too_many_images": "عدد الصور يتجاوز الحد (4 صور في الرسالة).",
    "dsc_upstream": "خطأ من واجهة DeepSeek خلف الموقع — أعد المحاولة لاحقًا.",
    "dsc_http": "خطأ HTTP/نقل غير متوقع.",
}


def error_from_payload(payload: Dict[str, Any], *, status: Optional[int] = None) -> ChatDeepError:
    """Build the most specific exception from a server error payload.

    ``payload`` is the decoded JSON body (or SSE ``error`` event data),
    shaped ``{"code": ..., "message": ..., "data": {...}}``.
    """
    code = str(payload.get("code") or "dsc_http")
    message = str(payload.get("message") or ERROR_INFO.get(code, code))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    cls = ERROR_CLASSES.get(code, ChatDeepError)
    err = cls(message, code=code, status=status if status is not None else payload.get("status"),
              data=data)
    return err
