"""Local token bridge — free, browser-free *Python*, powered by your own browser.

Idea: you already have a normal browser.  A tiny Tampermonkey userscript
(``browser/token-bridge.user.js``) runs on chat-deep.ai, keeps a small pool of
fresh Turnstile tokens produced by **the site's own widget in your own
browser**, and pushes them to a local HTTP server (127.0.0.1).  The Python
client pops tokens from that server — no Playwright, no solver fees, nothing
automated on the security check itself.

Run the bridge standalone::

    python -m chatdeep bridge --port 8787 --key mysecret

or let :class:`BridgeTokenProvider` embed it inside your script::

    provider = BridgeTokenProvider(embed=True, port=8787)
    client = ChatDeepClient(token_provider=provider)

Endpoints (localhost only):

``GET  /health``           liveness probe
``GET  /status``           pool state (the userscript polls this)
``POST /token``            userscript pushes ``{"token": "..."}``
``GET  /token?wait=SECS``  Python pops a fresh token (long-poll)

Security: binds 127.0.0.1 only; optional shared secret via ``X-Bridge-Key``
(or ``key`` field); requests carrying a foreign ``Origin`` header are
rejected so random websites can't feed or drain the pool from your browser.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Deque, Dict, Optional, Tuple

import requests

from .errors import TurnstileClientError
from .turnstile import TOKEN_TTL_S, TokenProvider

log = logging.getLogger("chatdeep.bridge")

DEFAULT_PORT = 8787
MAX_POOL = 10
ALLOWED_ORIGINS = ("https://chat-deep.ai", "http://chat-deep.ai")


class TokenPool:
    """Thread-safe FIFO of ``(token, received_at)`` pairs with TTL pruning."""

    def __init__(self, key: Optional[str] = None, max_pool: int = MAX_POOL):
        self.key = key
        self.max_pool = max_pool
        self._q: Deque[Tuple[str, float]] = deque()
        self._cv = threading.Condition()
        self.stats = {"pushed": 0, "taken": 0, "expired": 0, "rejected": 0}

    def push(self, token: str) -> int:
        now = time.time()
        with self._cv:
            self._prune(now)
            self._q.append((token.strip(), now))
            while len(self._q) > self.max_pool:
                self._q.popleft()
            self.stats["pushed"] += 1
            self._cv.notify_all()
            return len(self._q)

    def pop(self, wait_s: float = 0.0) -> Optional[str]:
        deadline = time.time() + max(0.0, wait_s)
        with self._cv:
            while True:
                self._prune(time.time())
                if self._q:
                    self.stats["taken"] += 1
                    return self._q.popleft()[0]
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cv.wait(min(remaining, 1.0))

    def _prune(self, now: float) -> None:
        while self._q and (now - self._q[0][1]) >= TOKEN_TTL_S:
            self._q.popleft()
            self.stats["expired"] += 1

    def snapshot(self) -> Dict:
        with self._cv:
            self._prune(time.time())
            oldest = self._q[0][1] if self._q else None
            return {
                "pool": len(self._q),
                "oldest_age_s": round(time.time() - oldest, 1) if oldest else None,
                "ttl_s": TOKEN_TTL_S,
                "key_required": bool(self.key),
                "stats": dict(self.stats),
            }


def _make_handler(pool: TokenPool):
    class BridgeHandler(BaseHTTPRequestHandler):
        server_version = "ChatDeepBridge/1.0"

        # -- helpers ---------------------------------------------------------
        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:                      # GM_xmlhttpRequest / curl
                return True
            return origin.rstrip("/") in ALLOWED_ORIGINS

        def _key_ok(self, body: Dict) -> bool:
            if not pool.key:
                return True
            given = self.headers.get("X-Bridge-Key") or body.get("key") or ""
            return given == pool.key

        def _send(self, code: int, obj: Dict, cors: bool = True) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            if cors:
                origin = self.headers.get("Origin")
                self.send_header("Access-Control-Allow-Origin",
                                 origin if origin in ALLOWED_ORIGINS else "*")
                self.send_header("Access-Control-Allow-Headers",
                                 "Content-Type, X-Bridge-Key")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                obj = json.loads(raw or b"{}")
                return obj if isinstance(obj, dict) else {}
            except json.JSONDecodeError:
                return {}

        def log_message(self, fmt, *args):  # quiet; use our logger
            log.debug(fmt, *args)

        # -- routes -----------------------------------------------------------
        def do_OPTIONS(self):  # noqa: N802 - http.server API
            self._send(200, {"ok": True})

        def do_GET(self):  # noqa: N802
            path, _, query = self.path.partition("?")
            if not self._origin_ok():
                pool.stats["rejected"] += 1
                return self._send(403, {"error": "origin-not-allowed"})
            if path == "/health":
                return self._send(200, {"ok": True, "service": "chatdeep-bridge"})
            if path == "/status":
                return self._send(200, pool.snapshot())
            if path == "/token":
                wait = 0.0
                for part in query.split("&"):
                    if part.startswith("wait="):
                        try:
                            wait = min(float(part[5:]), 120.0)
                        except ValueError:
                            pass
                if not self._key_ok({}):
                    return self._send(403, {"error": "bad-key"})
                token = pool.pop(wait_s=wait)
                if token is None:
                    return self._send(503, {
                        "error": "no-token",
                        "hint": "شغّل سكربت المتصفح على chat-deep.ai وانتظر امتلاء المخزون"})
                return self._send(200, {"token": token, "pool": pool.snapshot()["pool"]})
            return self._send(404, {"error": "not-found"})

        def do_POST(self):  # noqa: N802
            path = self.path.partition("?")[0]
            body = self._body()
            if not self._origin_ok():
                pool.stats["rejected"] += 1
                return self._send(403, {"error": "origin-not-allowed"})
            if path == "/token":
                if not self._key_ok(body):
                    pool.stats["rejected"] += 1
                    return self._send(403, {"error": "bad-key"})
                token = str(body.get("token") or "").strip()
                if not token:
                    return self._send(400, {"error": "empty-token"})
                n = pool.push(token)
                return self._send(200, {"ok": True, "pool": n})
            return self._send(404, {"error": "not-found"})

    return BridgeHandler


class BridgeServer:
    """The localhost token-bridge HTTP server (runs in a daemon thread)."""

    def __init__(self, port: int = DEFAULT_PORT, key: Optional[str] = None,
                 host: str = "127.0.0.1"):
        self.pool = TokenPool(key=key)
        self.host, self.port = host, port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "BridgeServer":
        if self._httpd:
            return self
        self._httpd = ThreadingHTTPServer((self.host, self.port),
                                          _make_handler(self.pool))
        # port=0 → the OS picked a free one; adopt it.
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True, name="cdpy-bridge")
        self._thread.start()
        log.info("bridge listening on http://%s:%d", self.host, self.port)
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


class BridgeTokenProvider(TokenProvider):
    """TokenProvider backed by the bridge.

    Args:
        port / host: where the bridge listens.
        key: shared secret (must match the server's ``--key``).
        embed: ``True`` → start an in-process bridge server; ``False`` →
               connect to an external one (started via ``chatdeep bridge``).
        wait_s: long-poll ceiling when the pool is empty.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 key: Optional[str] = None, embed: bool = False,
                 wait_s: float = 90.0):
        self.key = key
        self.wait_s = wait_s
        self._server: Optional[BridgeServer] = None
        self._http = requests.Session()
        if embed:
            self._server = BridgeServer(port=port, key=key, host=host).start()
            port = self._server.port      # honour port=0 (OS-assigned)
        self.base = f"http://{host}:{port}"

    # -- TokenProvider ----------------------------------------------------------
    def take_token(self) -> str:
        headers = {"X-Bridge-Key": self.key} if self.key else {}
        try:
            r = self._http.get(f"{self.base}/token",
                               params={"wait": int(self.wait_s)},
                               headers=headers, timeout=self.wait_s + 15)
        except requests.RequestException as exc:
            raise TurnstileClientError(
                f"تعذّر الوصول إلى جسر التوكن على {self.base}: {exc}\n"
                "شغّله عبر: python -m chatdeep bridge  (أو embed=True)",
                code="dsc_turnstile_client") from exc
        if r.status_code == 200:
            return r.json()["token"]
        if r.status_code == 403:
            raise TurnstileClientError("جسر التوكن رفض المفتاح/المصدر",
                                       code="dsc_turnstile_client")
        try:
            hint = r.json().get("hint", "")
        except ValueError:
            hint = ""
        raise TurnstileClientError(
            f"لا توكن متاحة من الجسر (HTTP {r.status_code}). {hint}\n"
            "تأكد أن متصفحك مفتوح على chat-deep.ai وأن سكربت الجسر يعمل.",
            code="dsc_turnstile_client")

    def prewarm(self) -> None:
        """Nudge: just query /status so the userscript sees recent activity."""
        try:
            self._http.get(f"{self.base}/status", timeout=5)
        except requests.RequestException:
            pass

    def status(self) -> Dict:
        r = self._http.get(f"{self.base}/status", timeout=5)
        return r.json()

    def close(self) -> None:
        if self._server:
            self._server.stop()
            self._server = None
        self._http.close()
