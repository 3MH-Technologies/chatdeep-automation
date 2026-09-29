"""Unit tests for the local token bridge (real localhost HTTP, no internet)."""
import json
import threading
import time

import pytest
import requests

from chatdeep.bridge import BridgeServer, BridgeTokenProvider, TokenPool
from chatdeep.errors import TurnstileClientError


@pytest.fixture()
def server():
    s = BridgeServer(port=0, key=None)          # port 0 → ephemeral
    # ThreadingHTTPServer with port 0 picks a free port
    s.start()
    s.port = s._httpd.server_address[1]
    yield s
    s.stop()


def _url(server, path):
    return f"http://127.0.0.1:{server.port}{path}"


def test_health_and_status(server):
    r = requests.get(_url(server, "/health"), timeout=5)
    assert r.json()["ok"] is True
    st = requests.get(_url(server, "/status"), timeout=5).json()
    assert st["pool"] == 0 and st["ttl_s"] == 240.0


def test_push_and_pop(server):
    r = requests.post(_url(server, "/token"), json={"token": "0.ABC"}, timeout=5)
    assert r.json() == {"ok": True, "pool": 1}
    got = requests.get(_url(server, "/token"), timeout=5).json()
    assert got["token"] == "0.ABC"
    # empty now
    r = requests.get(_url(server, "/token"), timeout=5)
    assert r.status_code == 503 and r.json()["error"] == "no-token"


def test_provider_take_token(server):
    prov = BridgeTokenProvider(port=server.port, wait_s=2)
    requests.post(_url(server, "/token"), json={"token": "0.XYZ"}, timeout=5)
    assert prov.take_token() == "0.XYZ"
    with pytest.raises(TurnstileClientError):
        prov.take_token()          # pool empty → clean error, not a crash
    prov.close()


def test_long_poll_waits_for_late_push(server):
    prov = BridgeTokenProvider(port=server.port, wait_s=5)

    def late():
        time.sleep(1.0)
        requests.post(_url(server, "/token"), json={"token": "0.LATE"}, timeout=5)

    t = threading.Thread(target=late, daemon=True)
    t.start()
    t0 = time.time()
    tok = prov.take_token()
    assert tok == "0.LATE" and 0.8 < time.time() - t0 < 4
    prov.close()


def test_key_required():
    s = BridgeServer(port=0, key="secret").start()
    port = s._httpd.server_address[1]
    try:
        r = requests.post(f"http://127.0.0.1:{port}/token", json={"token": "x"}, timeout=5)
        assert r.status_code == 403
        r = requests.post(f"http://127.0.0.1:{port}/token",
                          json={"token": "x"}, headers={"X-Bridge-Key": "secret"}, timeout=5)
        assert r.status_code == 200
        r = requests.get(f"http://127.0.0.1:{port}/token", timeout=5)
        assert r.status_code == 403
        prov = BridgeTokenProvider(port=port, key="secret", wait_s=1)
        assert prov.take_token() == "x"
        prov.close()
    finally:
        s.stop()


def test_foreign_origin_rejected(server):
    r = requests.post(_url(server, "/token"), json={"token": "evil"},
                      headers={"Origin": "https://malicious.example"}, timeout=5)
    assert r.status_code == 403
    r = requests.post(_url(server, "/token"), json={"token": "good"},
                      headers={"Origin": "https://chat-deep.ai"}, timeout=5)
    assert r.status_code == 200


def test_ttl_pruning(monkeypatch):
    pool = TokenPool()
    monkeypatch.setattr("chatdeep.bridge.TOKEN_TTL_S", 0.2)
    pool.push("old")
    time.sleep(0.3)
    assert pool.pop() is None
    assert pool.stats["expired"] == 1


def test_embedded_provider():
    prov = BridgeTokenProvider(port=0, embed=True, wait_s=2)
    requests.post(prov.base + "/token", json={"token": "0.EMBED"}, timeout=5)
    assert prov.take_token() == "0.EMBED"
    prov.close()
