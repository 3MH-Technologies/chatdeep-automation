"""مثال 2 — البث الحي: طباعة الإجابة حرفًا بحرف مع دوال ندائية.

التشغيل:  python examples/02_streaming_callbacks.py
"""
import sys

from chatdeep import ChatDeepClient

def main() -> None:
    with ChatDeepClient() as client:
        answer = client.chat(
            "اشرح الفرق بين Fast و Pro في ثلاث نقاط مختصرة.",
            mode="fast",
            on_reasoning=lambda: print("\n[يفكر...]", file=sys.stderr),
            on_delta=lambda chunk: (sys.stdout.write(chunk), sys.stdout.flush()),
            on_done=lambda data: print(f"\n\n[done: finish={data.get('finish')}]",
                                       file=sys.stderr),
        )
        if answer.incomplete:
            print("تنبيه: الإجابة غير مكتملة (finish=length)")
        print(f"زمن الاستدلال: {answer.reasoned_ms}ms · الإجمالي: {answer.total_ms}ms")

        # متابعة بسياق المحادثة (يُرسل آخر 20 رسالة تلقائيًا)
        print("\n— سؤال متابعة —")
        follow = client.ask("أعد صياغة النقطة الأولى بالعامية.")
        print(follow.content)

if __name__ == "__main__":
    main()
