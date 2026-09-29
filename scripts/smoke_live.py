#!/usr/bin/env python3
"""فحص حي شامل (Live smoke test) لنقاط chat-deep.ai الداخلية.

يفحص — دون استهلاك حصة الرسائل (ما لم تمرر --send):
  1. اكتشاف إعداد data-cdc من الصفحة
  2. GET status.php
  3. GET region.php
  4. POST dsc_session
  5. POST dsc/v2/chat بتوكن غير صالحة → نتوقع 403 dsc_turnstile (سلوك موثق)
  6. (--send اختياري) رسالة حقيقية عبر المحرك المضبوط — تستهلك رسالة واحدة

المحرك يُختار من البيئة (CHATDEEP_SOLVER/CHATDEEP_API_KEY أو
CHATDEEP_BRIDGE_PORT ...) أو من الوسائط أدناه.

التشغيل:
  python scripts/smoke_live.py                    # بلا إرسال حقيقي
  python scripts/smoke_live.py --send             # بمحرك البيئة المضبوط
  python scripts/smoke_live.py --send --engine bridge --bridge-port 8787
  python scripts/smoke_live.py --send --engine solver --solver capsolver --api-key KEY
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatdeep import ChatDeepClient                     # noqa: E402
from chatdeep.client import discover_config              # noqa: E402
from chatdeep.errors import ChatDeepError, TurnstileError  # noqa: E402


def step(n, title):
    print(f"\n[{n}] {title} ...", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="إرسال رسالة حقيقية واحدة")
    ap.add_argument("--headed", action="store_true", help="playwright بواجهة مرئية")
    ap.add_argument("--engine", choices=["auto", "manual", "solver", "bridge", "playwright"])
    ap.add_argument("--solver", choices=["capsolver", "2captcha", "yescaptcha"])
    ap.add_argument("--api-key")
    ap.add_argument("--bridge-port", type=int)
    ap.add_argument("--bridge-key")
    args = ap.parse_args()
    failures = 0

    step(1, "اكتشاف إعداد data-cdc")
    cfg = discover_config()
    print("   chat_url   :", cfg.chat_url)
    print("   session_url:", cfg.session_url)
    print("   sitekey    :", cfg.turnstile_sitekey)
    print("   حدود       :", cfg.max_chars, "حرف ·", cfg.max_images, "صور ·",
          "سياق", cfg.context, "· يومي", cfg.day_limit, "· Pro", cfg.pro_limit)
    failures += not (cfg.chat_url and cfg.turnstile_sitekey)

    client = ChatDeepClient(engine=args.engine, solver=args.solver, api_key=args.api_key,
                            bridge_port=args.bridge_port, bridge_key=args.bridge_key,
                            headless=not args.headed)

    step(2, "GET status.php")
    st = client.service_status()
    print("   ", json.dumps(st, ensure_ascii=False)[:200])
    failures += not st.get("ok")

    step(3, "GET region.php")
    print("    region =", client.fetch_region())

    step(4, "POST dsc_session")
    info = client.bootstrap()
    print("    nonce =", info.nonce, "· status =", info.status.state,
          "· paused =", info.paused)
    print("    quota =", info.quota)
    failures += not info.nonce

    step(5, "POST chat بتوكن غير صالحة (نتوقع 403 dsc_turnstile من الخادم)")
    from chatdeep.turnstile import ManualTokenProvider
    probe = ChatDeepClient(discover=False, cookie_store=None)
    probe._cfg = cfg
    probe.session_info = info
    probe.quota = info.quota
    # callable → every take (incl. the client's single silent retry) gets a
    # "fresh" but invalid token, so we observe the *server's* verdict.
    probe._provider = ManualTokenProvider(lambda: f"invalid-token-probe-{time.time_ns()}")
    try:
        probe.chat("probe", keep=False)
        print("    ✖ لم يُرفض الطلب غير المتوقع!")
        failures += 1
    except TurnstileError as e:
        print(f"    ✔ رُفض كما هو موثق: [{e.code}] HTTP {e.status} — {e.message[:80]}")
    except ChatDeepError as e:
        print(f"    ~ رفض بكود آخر: [{e.code}] {e.message[:100]}")
    finally:
        probe.close()

    if args.send:
        step(6, "إرسال رسالة حقيقية (المحرك المضبوط)")
        t0 = time.time()
        try:
            print("    المحرك:", type(client.provider).__name__)
            ans = client.chat("قل 'فحص حي ناجح' ولا شيء آخر.", mode="fast",
                              on_delta=lambda t: print(t, end="", flush=True))
            print(f"\n    ✔ {ans.finish} في {time.time() - t0:.1f}s · الحصة: {client.quota}")
        except ChatDeepError as e:
            print(f"\n    ✖ [{e.code}] {e.message}")
            failures += 1
    else:
        print("\n(تخطي الإرسال الحقيقي — مرر --send لاستهلاك رسالة واحدة)")

    client.close()
    print("\nالنتيجة:", "نجح كل شيء ✔" if not failures else f"{failures} إخفاق(ات) ✖")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
