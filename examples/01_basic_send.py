"""مثال 1 — أبسط إرسال: سؤال واحد وطباعة الجواب.

التشغيل:  python examples/01_basic_send.py
"""
from chatdeep import ChatDeepClient

def main() -> None:
    with ChatDeepClient() as client:
        client.bootstrap()
        print(f"الحصة قبل الإرسال: {client.quota}")

        answer = client.ask("عرّف بنفسك في جملة واحدة.")

        print(f"\nالجواب ({answer.mode} · {answer.total_ms}ms):")
        print(answer.content)
        print(f"\nالحصة بعد الإرسال: {client.quota}")

if __name__ == "__main__":
    main()
