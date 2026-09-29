"""Data models for the Chat-Deep.ai internal API.

Every model mirrors a payload that was reverse-engineered from the site's
``dsc-chat.js`` (plugin ``deepseek-chat`` v2.2.0) and verified against the
live endpoints.  See ``docs/API.md`` for the full endpoint documentation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Quota / status
# --------------------------------------------------------------------------


@dataclass
class Quota:
    """Per-visitor message quota returned by ``dsc_session`` and SSE ``done``.

    Attributes:
        day_used:   Messages consumed today (Fast + Pro + Compare legs).
        day_limit:  Daily cap (50 at the time of writing).
        pro_used:   Pro-mode messages consumed today.
        pro_limit:  Daily Pro cap (10 at the time of writing).
        day_left:   ``day_limit - day_used``.
        pro_left:   ``pro_limit - pro_used``.
        resets_at:  ISO-8601 UTC timestamp of the next reset (midnight UTC).
        unlimited:  ``True`` for privileged/admin visitors.
    """

    day_used: int = 0
    day_limit: int = 50
    pro_used: int = 0
    pro_limit: int = 10
    day_left: int = 50
    pro_left: int = 10
    resets_at: str = ""
    unlimited: bool = False

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Optional["Quota"]:
        if not d:
            return None
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def allows(self, mode: str = "fast", legs: int = 1) -> bool:
        """``True`` when *legs* messages of *mode* fit into the remaining quota."""
        if self.unlimited:
            return True
        if self.day_left < legs:
            return False
        if mode == "pro" and self.pro_left < legs:
            return False
        return True

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if self.unlimited:
            return "unlimited (admin)"
        return (
            f"today {self.day_used}/{self.day_limit} · "
            f"pro {self.pro_used}/{self.pro_limit} · resets {self.resets_at}"
        )


@dataclass
class ServiceStatus:
    """The little availability pill next to the chat input."""

    state: str = "neutral"  # ok | degraded | down | neutral
    label: str = "Unknown"


@dataclass
class Consent:
    """Regional notice state (EEA / UK / Switzerland visitors)."""

    required: bool = False
    given: bool = True


@dataclass
class SessionInfo:
    """Response of ``POST admin-ajax.php?action=dsc_session`` (body ``v=2``)."""

    nonce: str = ""
    access_chat: bool = False
    paused: bool = False
    status: ServiceStatus = field(default_factory=ServiceStatus)
    quota: Optional[Quota] = None
    consent: Consent = field(default_factory=Consent)
    helpdesk: bool = True
    raw: Dict[str, Any] = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionInfo":
        status = d.get("status") or {}
        consent = d.get("consent") or {}
        access = d.get("access") or {}
        return cls(
            nonce=d.get("nonce", ""),
            access_chat=bool(access.get("chat")),
            paused=bool(d.get("paused")),
            status=ServiceStatus(state=status.get("state", "neutral"),
                                 label=status.get("label", "Unknown")),
            quota=Quota.from_dict(d.get("quota")),
            consent=Consent(required=bool(consent.get("required")),
                            given=bool(consent.get("given", True))),
            helpdesk=d.get("helpdesk") is not False,
            raw=d,
        )


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """One chat message.

    ``images`` holds base64 *data URLs* (``data:image/jpeg;base64,...``)
    exactly like the browser client stores them after canvas re-encoding.
    Only the **most recent** user message keeps its images when the history
    is serialised (site behaviour, see ``payloadMessages`` in dsc-chat.js).
    """

    role: str  # "user" | "assistant"
    content: str = ""
    images: List[str] = field(default_factory=list)
    ts: int = field(default_factory=lambda: int(time.time() * 1000))
    mode: Optional[str] = None        # assistant answers remember their mode
    error: bool = False               # errored answers are dropped from context
    incomplete: bool = False          # finish == length | interrupted
    reasoned_ms: Optional[int] = None
    helpdesk: bool = False

    def to_payload(self, include_images: bool) -> Dict[str, Any]:
        """Serialise for the ``messages`` array of the chat request."""
        out: Dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.role == "user" and self.images:
            if include_images:
                out["images"] = list(self.images)
            else:
                n = len(self.images)
                suffix = f"[{n} image{'s' if n != 1 else ''} shared earlier]"
                out["content"] = (out["content"] + "\n\n" if out["content"] else "") + suffix
        return out

    @classmethod
    def user(cls, content: str, images: Optional[List[str]] = None) -> "ChatMessage":
        return cls(role="user", content=content, images=images or [])

    @classmethod
    def assistant(cls, content: str, **kw: Any) -> "ChatMessage":
        return cls(role="assistant", content=content, **kw)


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------

EVENT_REASONING = "reasoning"
EVENT_DELTA = "delta"
EVENT_DONE = "done"
EVENT_ERROR = "error"


@dataclass
class StreamEvent:
    """One Server-Sent-Event from ``POST /wp-json/dsc/v2/chat``.

    ``kind`` is one of ``reasoning`` / ``delta`` / ``done`` / ``error``
    (unknown kinds are passed through with their raw name).
    """

    kind: str
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Delta convenience accessor (``{"t": "..."}``)."""
        t = self.data.get("t")
        return t if isinstance(t, str) else ""


@dataclass
class ChatAnswer:
    """A fully-consumed assistant answer."""

    content: str = ""
    mode: str = "fast"
    helpdesk: bool = False
    finish: str = "stop"          # stop | length | interrupted | error
    incomplete: bool = False
    reasoned_ms: Optional[int] = None
    total_ms: Optional[int] = None
    quota: Optional[Quota] = None
    started_reasoning_at: Optional[float] = None

    def to_message(self) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=self.content,
            mode=self.mode,
            incomplete=self.incomplete,
            reasoned_ms=self.reasoned_ms,
            helpdesk=self.helpdesk,
            error=self.finish == "error",
        )


@dataclass
class CompareResult:
    """Outcome of a Fast-vs-Pro compare request (costs 2 quota messages)."""

    prompt: str
    fast: Optional[ChatAnswer] = None
    pro: Optional[ChatAnswer] = None
    fast_error: Optional[str] = None
    pro_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.fast is not None and self.pro is not None


@dataclass
class SiteConfig:
    """The ``data-cdc`` widget configuration embedded in every chat page.

    Discovered live by :func:`chatdeep.client.discover_config` so the tool
    keeps working when the site rotates versions or limits.
    """

    session_url: str
    chat_url: str
    notice_url: str
    turnstile_sitekey: str
    full_page_url: str
    name: str = "Chat-Deep Assistant"
    max_chars: int = 6000
    max_images: int = 4
    max_image_bytes: int = 5 * 1024 * 1024
    context: int = 20
    day_limit: int = 50
    pro_limit: int = 10
    voice: int = 1
    privacy_mode: str = "user_choice"
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_cdc(cls, cdc: Dict[str, Any]) -> "SiteConfig":
        return cls(
            session_url=cdc.get("session", ""),
            chat_url=cdc.get("chat", ""),
            notice_url=cdc.get("notice", ""),
            turnstile_sitekey=cdc.get("ts", ""),
            full_page_url=cdc.get("full", ""),
            name=cdc.get("name", "Chat-Deep Assistant"),
            max_chars=int(cdc.get("maxChars", 6000)),
            max_images=int(cdc.get("maxImages", 4)),
            max_image_bytes=int(cdc.get("maxImageBytes", 5242880)),
            context=int(cdc.get("context", 20)),
            day_limit=int(cdc.get("dayLimit", 50)),
            pro_limit=int(cdc.get("proLimit", 10)),
            voice=int(cdc.get("voice", 1)),
            privacy_mode=str(cdc.get("privacyMode", "user_choice")),
            raw=cdc,
        )
