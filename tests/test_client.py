"""Unit tests for payload building, models and error mapping (no network)."""
import pytest

from chatdeep.client import ChatDeepClient
from chatdeep.errors import (
    ChatDeepError, NonceError, QuotaExceeded, TurnstileError, ValidationError,
    error_from_payload,
)
from chatdeep.models import ChatMessage, Quota, SessionInfo, SiteConfig


@pytest.fixture()
def client():
    # discover=False → no network; no cookie store
    return ChatDeepClient(discover=False, cookie_store=None)


def test_context_window_trims_to_last_20(client):
    history = [ChatMessage.user(f"m{i}") for i in range(30)]
    payload = client.build_payload_messages(history)
    assert len(payload) == client.cfg.context == 20
    assert payload[-1]["content"] == "m29"


def test_images_only_on_last_image_message(client):
    img = "data:image/png;base64,AAAA"
    history = [
        ChatMessage.user("أول", images=[img]),
        ChatMessage.assistant("جواب"),
        ChatMessage.user("ثانٍ", images=[img, img]),
    ]
    payload = client.build_payload_messages(history)
    # last user message keeps real images
    assert payload[2]["images"] == [img, img]
    # earlier image message gets the placeholder text (site behaviour)
    assert "images" not in payload[0]
    assert "[1 image shared earlier]" in payload[0]["content"]


def test_errored_messages_dropped_from_context(client):
    history = [
        ChatMessage.user("س"),
        ChatMessage.assistant("", error=True),
        ChatMessage.user("س2"),
    ]
    payload = client.build_payload_messages(history)
    assert len(payload) == 2


def test_quota_allows():
    q = Quota(day_used=49, day_limit=50, day_left=1, pro_left=0)
    assert q.allows("fast")
    assert not q.allows("pro")
    assert not q.allows("fast", legs=2)
    assert Quota(unlimited=True).allows("pro", legs=100)


def test_session_info_from_dict():
    info = SessionInfo.from_dict({
        "nonce": "abc123", "access": {"chat": True}, "paused": False,
        "status": {"state": "ok", "label": "Online"},
        "quota": {"day_used": 1, "day_limit": 50, "pro_used": 0, "pro_limit": 10,
                  "day_left": 49, "pro_left": 10, "resets_at": "x", "unlimited": False},
        "consent": {"required": True, "given": False}, "helpdesk": True,
    })
    assert info.nonce == "abc123" and info.access_chat
    assert info.consent.required and not info.consent.given
    assert info.quota.day_left == 49


def test_error_mapping():
    e = error_from_payload({"code": "dsc_bad_nonce", "message": "m"}, status=403)
    assert isinstance(e, NonceError)
    e = error_from_payload({"code": "dsc_turnstile", "message": "m"}, status=403)
    assert isinstance(e, TurnstileError) and e.status == 403
    e = error_from_payload({"code": "dsc_pro_limit", "message": "m"})
    assert isinstance(e, QuotaExceeded)
    e = error_from_payload({"code": "dsc_totally_new_code", "message": "m"})
    assert type(e) is ChatDeepError and e.code == "dsc_totally_new_code"


def test_quota_guard_falls_back_to_fast(client):
    client.quota = Quota(day_used=5, day_limit=50, day_left=45,
                         pro_used=10, pro_limit=10, pro_left=0)
    assert client._quota_guard("pro") == "fast"      # fallback_to_fast default True
    client.fallback_to_fast = False
    with pytest.raises(QuotaExceeded):
        client._quota_guard("pro")


def test_quota_guard_day_limit(client):
    client.quota = Quota(day_used=50, day_limit=50, day_left=0)
    with pytest.raises(QuotaExceeded):
        client._quota_guard("fast")


def test_validation_too_long(client):
    msg = ChatMessage.user("x" * (client.cfg.max_chars + 1))
    with pytest.raises(ValidationError):
        client._validate(msg)


def test_validation_too_many_images(client):
    msg = ChatMessage.user("hi", images=["data:image/png;base64,"] * 5)
    with pytest.raises(ValidationError):
        client._validate(msg)


def test_site_config_from_cdc():
    cfg = SiteConfig.from_cdc({
        "session": "s", "chat": "c", "notice": "n", "ts": "key", "full": "f",
        "maxChars": 100, "maxImages": 2, "maxImageBytes": 10, "context": 5,
        "dayLimit": 7, "proLimit": 3,
    })
    assert cfg.max_chars == 100 and cfg.context == 5 and cfg.day_limit == 7


def test_honeypot_and_body_shape(client, monkeypatch):
    """The outgoing chat body must match the site's schema exactly."""
    captured = {}

    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}

        def iter_content(self, chunk_size=None):
            yield b'event: done\ndata: {"finish":"stop"}\n\n'

        def close(self):
            pass

    class FakeProvider:
        def take_token(self):
            return "TOKEN"

        def prewarm(self):
            pass

        def close(self):
            pass

    def fake_post(url, data=None, headers=None, stream=None, timeout=None):
        import json as _json
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = _json.loads(data)
        return FakeResp()

    client.session_info = SessionInfo.from_dict(
        {"nonce": "N0NCE", "access": {"chat": True}, "paused": False,
         "status": {}, "consent": {"required": False, "given": True}})
    client._provider = FakeProvider()
    monkeypatch.setattr(client._http, "post", fake_post)

    ans = client.chat("اختبار", mode="pro", keep=False)
    body = captured["body"]
    assert set(body) == {"messages", "mode", "helpdesk", "ts", "hp"}
    assert body["ts"] == "TOKEN" and body["hp"] == ""
    assert body["mode"] == "pro" and body["helpdesk"] is False
    assert body["messages"][-1] == {"role": "user", "content": "اختبار"}
    assert captured["headers"]["X-WP-Nonce"] == "N0NCE"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert ans.finish == "stop"
