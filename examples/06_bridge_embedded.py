"""مثال 6 — الجسر المدمج: أتمتة بلا متصفح في بايثون، التوكن من متصفحك أنت.

يشغّل هذا السكربت خادم الجسر داخل العملية نفسها (embed=True) ثم ينتظر
(long-poll) حتى يدفع سكربت المتصفح (token-bridge.user.js) توكن طازجة.

المتطلبات:
  1) ثبّت Tampermonkey والصق محتوى `python -m chatdeep userscript`
  2) اضبط في السكربت: bridge='http://127.0.0.1:8787' و key='s3cret'
  3) افتح تبويبًا على https://chat-deep.ai/

التشغيل:  python examples/06_bridge_embedded.py
"""
import sys

from chatdeep import BridgeTokenProvider, ChatDeepClient

def main() -> None:
    provider = BridgeTokenProvider(port=8787, key="s3cret", embed=True, wait_s=120)
    print(f"🌉 الجسر يعمل على {provider.base} — افتح chat-deep.ai في متصفحك…")

    with ChatDeepClient(token_provider=provider) as client:
        answer = client.chat(
            "قل: الجسر يعمل!",
            mode="fast",
            on_delta=lambda t: (sys.stdout.write(t), sys.stdout.flush()),
        )
        print(f"\n[{answer.mode} · {answer.total_ms}ms] · الحصة: {client.quota}")

if __name__ == "__main__":
    main()
