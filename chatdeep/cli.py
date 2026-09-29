"""Command-line interface for the Chat-Deep.ai automation client.

Usage::

    chatdeep                          # interactive REPL (default)
    chatdeep ask "سؤالك" --mode pro   # one-shot
    chatdeep ask "قارن" --compare     # Fast vs Pro side by side
    chatdeep status                   # service status + quota
    chatdeep doctor                   # environment diagnostics
    chatdeep bridge                   # run the local token bridge server
    chatdeep userscript               # print the browser userscript

Token engines (no browser needed for solver/bridge)::

    # 1) خدمة حل برمجية (CapSolver / 2Captcha / YesCaptcha)
    export CHATDEEP_SOLVER=capsolver CHATDEEP_API_KEY=...
    # 2) جسر محلي يغذيه متصفحك الحقيقي (مجاني)
    chatdeep bridge &            # ثم ثبّت userscript على chat-deep.ai
    export CHATDEEP_BRIDGE_PORT=8787
    # 3) توكن يدوية (استخدام واحد)
    chatdeep ask "مرحبا" --token "0.xxxx..."

Inside the REPL: ``/help`` lists every slash command.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .client import ChatDeepClient
from .errors import ChatDeepError, ERROR_INFO, QuotaExceeded
from .models import ChatAnswer, CompareResult

BANNER = r"""
  ____ _           _   ____             _     _
 / ___| |__   __ _| |_|  _ \  ___  __ _| |__ | |__
| |   | '_ \ / _` | __| | | |/ _ \/ _` | '_ \| '_ \
| |___| | | | (_| | |_| |_| |  __/ (_| | |_) | | | |
 \____|_| |_|\__,_|\__|____/ \___|\__,_|_.__/|_| |_|
   أداة أتمتة شات احترافية لموقع chat-deep.ai  v{ver}
   تطوير 3MH TECHNOLOGIES · https://3mh.pages.dev · t.me/j49_c
"""

REPL_HELP = """الأوامر المتاحة داخل الجلسة:
  /mode [fast|pro]     عرض أو تغيير الوضع (Pro = استدلال أعمق، 10/يوم)
  /compare on|off      تفعيل/تعطيل وضع المقارنة (إرسال واحد ← إجابتان، برسالتين)
  /image <path> [...]  إرفاق صور للرسالة التالية (حتى 4، ≤5MB لكل صورة)
  /images              عرض الصور المرفوعة حاليًا
  /system <text>       تعيين موجه نظام يُحقن مع أول رسالة (محليًا)
  /engine              عرض محرك التوكن الحالي وحالته
  /reset               مسح سجل المحادثة (السياق)
  /quota               عرض الحصة المتبقية
  /status              حالة خدمة DeepSeek حسب مراقبة الموقع
  /save <file.json>    تصدير سجل المحادثة
  /headed on|off       متصفح مرئي (محرك playwright فقط)
  /token               اختبار: جلب توكن Turnstile واحدة وعرضها
  /help                هذه المساعدة
  /exit أو /quit       الخروج
"""

USERSCRIPT_PATH = Path(__file__).parent / "browser" / "token-bridge.user.js"


def _fmt_ms(ms: Optional[int]) -> str:
    return f"{ms / 1000:.1f}s" if ms else "—"


def _print_answer_meta(ans: ChatAnswer) -> None:
    meta = f"[{ans.mode} · {_fmt_ms(ans.total_ms)}]"
    if ans.reasoned_ms:
        meta += f" [استدلال {_fmt_ms(ans.reasoned_ms)}]"
    if ans.incomplete:
        meta += " [غير مكتمل]"
    print(f"\n\033[90m{meta}\033[0m")


class Repl:
    """Interactive chat session."""

    def __init__(self, client: ChatDeepClient, raw: bool = False):
        self.c = client
        self.raw = raw
        self.mode = "fast"
        self.compare_on = False
        self.pending_images: List[str] = []
        self._reasoning_shown = False

    # -- stream rendering ---------------------------------------------------
    def _on_reasoning(self) -> None:
        if not self._reasoning_shown:
            self._reasoning_shown = True
            print("\033[90mيفكّر…\033[0m", end="", flush=True)

    def _on_delta(self, text: str) -> None:
        if self._reasoning_shown:
            self._reasoning_shown = False
            print("\r\033[2K", end="")  # clear "يفكّر…"
        sys.stdout.write(text if self.raw else text)
        sys.stdout.flush()

    # -- main loop -----------------------------------------------------------
    def run(self) -> int:
        print(BANNER.format(ver=__version__))
        try:
            info = self.c.bootstrap()
            print(f"\033[92m●\033[0m الجلسة جاهزة — {info.status.label} · الحصة: {info.quota}")
        except ChatDeepError as e:
            print(f"\033[93mتحذير: تعذّر تهيئة الجلسة ({e.code}: {e.message})\033[0m")
        print(f"محرك التوكن: {self.c.engine or 'auto'} — /engine للتفاصيل · /help للأوامر · /exit للخروج\n")

        while True:
            try:
                line = input(f"\033[96m[{self.mode}{'/cmp' if self.compare_on else ''}] › \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                self.c.prewarm()  # request a token while the user types
                continue
            if line.startswith("/"):
                stop = self._command(line)
                if stop:
                    break
                continue
            self._send(line)
            self.c.prewarm()

        self.c.close()
        print("إلى اللقاء 👋")
        return 0

    # -- sending ---------------------------------------------------------------
    def _send(self, text: str) -> None:
        images, self.pending_images = self.pending_images, []
        print(f"\033[94m{self.c.cfg.name}:\033[0m ", end="", flush=True)
        try:
            if self.compare_on:
                self._compare(text, images)
            else:
                ans = self.c.chat(text, mode=self.mode, images=images or None,
                                  on_reasoning=self._on_reasoning, on_delta=self._on_delta)
                self._print_tail(ans)
        except QuotaExceeded as e:
            print(f"\n\033[91m⛔ {e.message}\033[0m")
        except ChatDeepError as e:
            hint = ERROR_INFO.get(e.code, "")
            print(f"\n\033[91m✖ [{e.code}] {e.message}\033[0m")
            if hint:
                print(f"\033[90m  {hint}\033[0m")
        except BrokenPipeError:  # pragma: no cover
            pass

    def _compare(self, text: str, images: List[str]) -> None:
        print("\n\033[90m(مقارنة Fast × Pro — تُحتسب رسالتان)\033[0m")
        res: CompareResult = self.c.compare(text, images=images or None)
        for mode in ("fast", "pro"):
            ans = getattr(res, mode)
            err = getattr(res, f"{mode}_error")
            title = "⚡ Fast" if mode == "fast" else "🧠 Pro"
            print(f"\n\033[1m{title}\033[0m")
            if ans:
                print(ans.content)
                self._print_tail(ans)
            else:
                print(f"\033[91m✖ {err}\033[0m")

    def _print_tail(self, ans: ChatAnswer) -> None:
        _print_answer_meta(ans)
        q = self.c.quota
        if q:
            print(f"\033[90mالحصة: اليوم {q.day_used}/{q.day_limit} · Pro {q.pro_used}/{q.pro_limit}\033[0m")

    # -- slash commands -----------------------------------------------------------
    def _command(self, line: str) -> bool:
        parts = line.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if cmd in ("/exit", "/quit", "/q"):
            return True
        if cmd == "/help":
            print(REPL_HELP)
        elif cmd == "/mode":
            if arg in ("fast", "pro"):
                self.mode = arg
                print(f"الوضع الآن: {arg}")
            else:
                print(f"الوضع الحالي: {self.mode} — للتغيير: /mode fast|pro")
        elif cmd == "/compare":
            self.compare_on = arg.strip().lower() in ("on", "1", "true", "")
            print(f"المقارنة: {'مفعّلة' if self.compare_on else 'معطّلة'}")
        elif cmd == "/image":
            paths = arg.split()
            missing = [p for p in paths if not Path(p).expanduser().exists()]
            if missing:
                print(f"\033[91mملفات غير موجودة: {', '.join(missing)}\033[0m")
            else:
                self.pending_images += [str(Path(p).expanduser()) for p in paths]
                print(f"أُرفقت {len(paths)} صورة للرسالة التالية (الإجمالي {len(self.pending_images)}/4)")
        elif cmd == "/images":
            print("\n".join(self.pending_images) or "لا صور مرفقة")
        elif cmd == "/system":
            self.c.system_prompt = arg or None
            print("تم تعيين موجه النظام" if arg else "تم إلغاء موجه النظام")
        elif cmd == "/engine":
            _print_engine_info(self.c)
        elif cmd == "/reset":
            self.c.reset_history()
            print("مُسح سجل المحادثة")
        elif cmd == "/quota":
            q = self.c.quota
            print(f"الحصة: {q}" if q else "لا توجد بيانات حصة بعد")
        elif cmd == "/status":
            try:
                st = self.c.service_status()
                state = {"green": "🟢 يعمل", "yellow": "🟡 مشاكل جزئية", "red": "🔴 متعثر"}.get(
                    st.get("state", ""), st.get("state"))
                print(f"{state} · استجابة {st.get('latency_ms', 0) / 1000:.1f}s · "
                      f"نجاح {st.get('success_pct', 0):.1f}% (24 ساعة) · "
                      f"الرسمي: {st.get('official', '?')}")
            except ChatDeepError as e:
                print(f"✖ {e.message}")
        elif cmd == "/save":
            path = Path(arg or f"chatdeep-{int(time.time())}.json").expanduser()
            data = [m.__dict__ for m in self.c.history]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"حُفظ {len(data)} رسالة في {path}")
        elif cmd == "/headed":
            headed = arg.strip().lower() in ("on", "1", "true", "")
            self.c.set_headless(headless=not headed)
            print(f"محرك playwright: {'مرئي' if headed else 'خفي'} (لا يؤثر على solver/bridge)")
        elif cmd == "/token":
            try:
                t0 = time.time()
                tok = self.c.provider.take_token()
                print(f"✔ توكن ({len(tok)} حرفًا) في {time.time() - t0:.1f}s: {tok[:40]}…")
            except ChatDeepError as e:
                print(f"✖ {e.message}")
        else:
            print(f"أمر غير معروف: {cmd} — جرّب /help")
        return False


def _print_engine_info(client: ChatDeepClient) -> None:
    prov = client._provider
    if prov is None:
        print(f"المحرك المطلوب: {client.engine or 'auto'} (لم يُنشأ بعد — سيُختار عند أول إرسال)")
        return
    name = type(prov).__name__
    extra = ""
    if hasattr(prov, "name"):                 # solver
        extra = f" · الخدمة: {prov.name}"
        try:
            extra += f" · الرصيد: {prov.get_balance()}"
        except Exception:
            pass
    elif hasattr(prov, "base"):               # bridge
        try:
            st = prov.status()
            extra = f" · {prov.base} · المخزون: {st.get('pool', '?')}"
        except Exception:
            extra = f" · {prov.base} · ✖ الجسر غير متاح"
    print(f"المزود النشط: {name}{extra}")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_ask(client: ChatDeepClient, args: argparse.Namespace) -> int:
    images = args.image or []
    if args.compare:
        res = client.compare(args.message, images=images)
        if args.json:
            print(json.dumps({
                "prompt": res.prompt,
                "fast": {k: v for k, v in res.fast.__dict__.items() if k != "quota"} if res.fast else None,
                "pro": {k: v for k, v in res.pro.__dict__.items() if k != "quota"} if res.pro else None,
                "fast_error": res.fast_error, "pro_error": res.pro_error,
            }, ensure_ascii=False, default=str, indent=1))
        else:
            for mode, label in (("fast", "⚡ Fast"), ("pro", "🧠 Pro")):
                ans, err = getattr(res, mode), getattr(res, f"{mode}_error")
                print(f"\n\033[1m{label}\033[0m")
                print(ans.content if ans else f"✖ {err}")
        return 0 if res.ok else 1

    def delta(t: str) -> None:
        if not args.json:
            sys.stdout.write(t)
            sys.stdout.flush()

    ans = client.chat(args.message, mode=args.mode, images=images or None,
                      helpdesk=args.helpdesk, use_history=not args.no_history,
                      on_reasoning=None if args.json else lambda: print("\033[90mيفكّر…\033[0m"),
                      on_delta=delta)
    if args.json:
        out = {k: v for k, v in ans.__dict__.items() if k != "quota"}
        out["quota"] = client.quota.__dict__ if client.quota else None
        print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
    else:
        _print_answer_meta(ans)
    return 0


def cmd_status(client: ChatDeepClient, args: argparse.Namespace) -> int:
    try:
        st = client.service_status()
    except ChatDeepError as e:
        print(f"✖ {e.message}")
        return 1
    state = {"green": "🟢 يعمل", "yellow": "🟡 مشاكل جزئية", "red": "🔴 متعثر"}.get(
        st.get("state", ""), str(st.get("state")))
    checked = st.get("checked", 0)
    ago = max(0, round((st.get("now", time.time()) - checked) / 60))
    print("حالة DeepSeek (مراقبة مستقلة من خادم chat-deep.ai):")
    print(f"  الحالة:        {state}")
    print(f"  زمن الاستجابة: {st.get('latency_ms', 0) / 1000:.1f}s")
    print(f"  النجاح (24س):  {st.get('success_pct', 0):.1f}% من {st.get('checks_24h', 0)} فحصًا")
    print(f"  الصفحة الرسمية: {st.get('official', '?')}")
    print(f"  آخر فحص:       قبل {ago} دقيقة")
    try:
        info = client.bootstrap()
        print(f"\nجلستك: {info.status.label} · nonce {info.nonce[:6]}…")
        if info.quota:
            print(f"الحصة: {info.quota}")
        print(f"المنطقة: {client.fetch_region()} · إشعار الخصوصية مطلوب: "
              f"{'نعم' if info.consent.required else 'لا'}")
    except ChatDeepError as e:
        print(f"\n✖ الجلسة: {e.message}")
    return 0


def cmd_doctor(client: ChatDeepClient, args: argparse.Namespace) -> int:
    ok = True

    def check(label: str, fn) -> None:
        nonlocal ok
        try:
            detail = fn()
            print(f"  \033[92m✔\033[0m {label}" + (f" — {detail}" if detail else ""))
        except Exception as e:
            ok = False
            print(f"  \033[91m✖\033[0m {label} — {e}")

    print("فحص البيئة:\n")
    check("الاتصال بالموقع",
          lambda: f"status.php OK · الحالة {client.service_status().get('state', '?')}")
    check("اكتشاف الإعدادات (data-cdc)",
          lambda: f"sitekey {client.cfg.turnstile_sitekey[:12]}… · حدود "
                  f"{client.cfg.max_chars} حرف/{client.cfg.max_images} صور")
    check("جلسة الدردشة (dsc_session)",
          lambda: f"nonce {client.bootstrap().nonce[:6]}… · الحصة {client.quota}")

    # Which token engine will be used?
    from .providers import build_token_provider
    engine_label = client.engine or "auto"
    try:
        prov = client.provider
        pname = type(prov).__name__
        check(f"محرك التوكن ({engine_label})", lambda: pname)

        if hasattr(prov, "get_balance"):          # solver
            check("رصيد خدمة الحل", lambda: f"{prov.get_balance()} وحدة · الخدمة {prov.name}")
        elif hasattr(prov, "status"):             # bridge
            st = prov.status()
            check("جسر التوكن",
                  lambda: f"{prov.base} · المخزون {st.get('pool', 0)} توكن"
                          + (" — جاهز" if st.get("pool", 0) > 0 else
                             " — فارغ! افتح chat-deep.ai في متصفحك (سكربت الجسر سيملؤه)"))
        elif pname == "PlaywrightTokenProvider":
            t0 = time.time()
            tok = prov.take_token()
            check("حصاد توكن (متصفح)", lambda: f"{len(tok)} حرفًا في {time.time() - t0:.1f}s")
        else:
            check("المزود", lambda: "يدوي — توكن واحدة لكل استخدام")
    except ChatDeepError as e:
        ok = False
        print(f"  \033[91m✖\033[0m محرك التوكن ({engine_label}) — {e.message}")

    try:
        import PIL  # noqa: F401
        check("Pillow (إرفاق الصور)", lambda: "مثبت")
    except ImportError:
        print("  \033[93m!\033[0m Pillow غير مثبت — إرفاق الصور معطّل (pip install pillow)")

    print("\nالنتيجة:",
          "\033[92mكل شيء جاهز ✔\033[0m" if ok else "\033[91mتوجد مشاكل — راجع الأعلى\033[0m")
    return 0 if ok else 1


def cmd_bridge(args: argparse.Namespace) -> int:
    from .bridge import BridgeServer
    server = BridgeServer(port=args.port, key=args.key).start()
    print(f"""
🌉 جسر التوكن يعمل على {server.url}  (Ctrl+C للإيقاف)

خطوات التشغيل:
  1) ثبّت Tampermonkey في متصفحك
  2) أنشئ سكربتًا جديدًا والصق محتوى:  chatdeep userscript
     (أو الملف chatdeep/browser/token-bridge.user.js)
  3) في إعدادات السكربت أعلى الملف: ضع{" المفتاح " + args.key if args.key else " المفتاح ''"}
  4) افتح أي صفحة على https://chat-deep.ai/ واترك التبويب يعمل
  5) في طرفية أخرى:  export CHATDEEP_BRIDGE_PORT={args.port}  ثم شغّل chatdeep

حالة المخزون (تحديث كل 5 ثوانٍ):""", flush=True)
    try:
        while True:
            time.sleep(5)
            snap = server.pool.snapshot()
            print(f"  [{time.strftime('%H:%M:%S')}] pool={snap['pool']} · "
                  f"pushed={snap['stats']['pushed']} · taken={snap['stats']['taken']} · "
                  f"expired={snap['stats']['expired']} · rejected={snap['stats']['rejected']}",
                  flush=True)
    except KeyboardInterrupt:
        print("\nإيقاف الجسر…")
    finally:
        server.stop()
    return 0


def cmd_userscript(args: argparse.Namespace) -> int:
    try:
        src = USERSCRIPT_PATH.read_text(encoding="utf-8")
    except OSError:
        print("✖ ملف السكربت غير موجود في الحزمة", file=sys.stderr)
        return 1
    if args.save:
        out = Path(args.save).expanduser()
        out.write_text(src, encoding="utf-8")
        print(f"✔ حُفظ في {out} — اسحبه إلى Tampermonkey أو أنشئ سكربتًا جديدًا والصقه")
        return 0
    print(src)
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_engine_flags(p: argparse.ArgumentParser, suppress_defaults: bool = False) -> None:
    d = argparse.SUPPRESS if suppress_defaults else None
    p.add_argument("--engine", choices=["auto", "manual", "solver", "bridge", "playwright"],
                   default=d, help="محرك جلب توكن Turnstile (افتراضي: auto حسب البيئة)")
    p.add_argument("--solver", choices=["capsolver", "2captcha", "yescaptcha"], default=d,
                   help="خدمة الحل البرمجية (مع --api-key أو CHATDEEP_API_KEY)")
    p.add_argument("--api-key", default=d, help="مفتاح API لخدمة الحل")
    p.add_argument("--bridge-port", type=int, default=d, help="منفذ جسر التوكن المحلي")
    p.add_argument("--bridge-key", default=d, help="المفتاح السري لجسر التوكن")
    p.add_argument("--embed-bridge", action="store_true", default=d if suppress_defaults else False,
                   help="تشغيل خادم الجسر داخل العملية نفسها")
    p.add_argument("--token", default=d, help="توكن Turnstile يدوية (استخدام واحد)")
    p.add_argument("--headed", action="store_true", default=d if suppress_defaults else False,
                   help="محرك playwright بواجهة مرئية (لحل التحديات تفاعليًا)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chatdeep",
        description="أداة أتمتة شات احترافية لموقع chat-deep.ai (Python) — بلا متصفح",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="أمثلة:\n"
               "  chatdeep ask \"لخّص لي مقالًا\" --mode pro\n"
               "  chatdeep ask \"ما هذا؟\" --image photo.png --json\n"
               "  chatdeep ask \"قارن الوضعين\" --compare\n"
               "  CHATDEEP_SOLVER=capsolver CHATDEEP_API_KEY=... chatdeep\n"
               "  chatdeep bridge --port 8787 --key secret\n"
               "  chatdeep status\n")
    p.add_argument("--version", action="version",
                   version=f"chatdeep {__version__} · 3MH TECHNOLOGIES "
                           f"(https://3mh.pages.dev · t.me/j49_c)")
    p.add_argument("--verbose", "-v", action="count", default=0, help="تسجيلات تصحيح")
    _add_engine_flags(p)
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("ask", help="إرسال رسالة واحدة والخروج")
    a.add_argument("message", help="نص الرسالة")
    a.add_argument("--mode", choices=["fast", "pro"], default="fast")
    a.add_argument("--image", action="append", help="صورة مرفقة (يمكن التكرار حتى 4)")
    a.add_argument("--compare", action="store_true", help="Fast و Pro جنبًا إلى جنب (رسالتان)")
    a.add_argument("--helpdesk", action="store_true", help="وضع الأسئلة عن DeepSeek نفسه")
    a.add_argument("--system", help="موجه نظام يُحقن قبل أول رسالة")
    a.add_argument("--no-history", action="store_true", help="تجاهل سجل المحادثة الحالي")
    a.add_argument("--json", action="store_true", help="إخراج JSON (للسكربتات)")
    _add_engine_flags(a, suppress_defaults=True)

    s = sub.add_parser("status", help="حالة الخدمة والحصة")
    _add_engine_flags(s, suppress_defaults=True)
    dd = sub.add_parser("doctor", help="فحص البيئة والتشخيص")
    _add_engine_flags(dd, suppress_defaults=True)
    r = sub.add_parser("repl", help="الجلسة التفاعلية (الافتراضي)")
    r.add_argument("--raw", action="store_true", help="عرض Markdown خام")
    _add_engine_flags(r, suppress_defaults=True)

    b = sub.add_parser("bridge", help="تشغيل خادم جسر التوكن المحلي")
    b.add_argument("--port", type=int, default=8787)
    b.add_argument("--key", default=None, help="مفتاح سري اختياري (X-Bridge-Key)")

    u = sub.add_parser("userscript", help="طباعة/حفظ سكربت متصفح الجسر")
    u.add_argument("--save", metavar="PATH", help="حفظ السكربت في ملف بدل الطباعة")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not any(a in ("ask", "status", "doctor", "repl", "bridge", "userscript") for a in argv):
        argv.append("repl")   # no subcommand → interactive session

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else (logging.INFO if args.verbose else logging.WARNING),
        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "bridge":
        return cmd_bridge(args)
    if args.cmd == "userscript":
        return cmd_userscript(args)

    client = ChatDeepClient(
        engine=getattr(args, "engine", None),
        solver=getattr(args, "solver", None),
        api_key=getattr(args, "api_key", None),
        manual_token=getattr(args, "token", None),
        bridge_port=getattr(args, "bridge_port", None),
        bridge_key=getattr(args, "bridge_key", None),
        bridge_embed=getattr(args, "embed_bridge", None) or None,
        headless=not getattr(args, "headed", False),
    )
    if getattr(args, "system", None):
        client.system_prompt = args.system

    try:
        if args.cmd == "ask":
            return cmd_ask(client, args)
        if args.cmd == "status":
            return cmd_status(client, args)
        if args.cmd == "doctor":
            return cmd_doctor(client, args)
        return Repl(client, raw=getattr(args, "raw", False)).run()
    except ChatDeepError as e:
        print(f"\033[91m✖ [{e.code}] {e.message}\033[0m", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        client.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
