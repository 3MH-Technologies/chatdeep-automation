"""مثال 4 — المقارنة: إرسال واحد يعود بإجابتين (Fast و Pro) على التوازي.

يستهلك رسالتين من الحصة اليومية (واحدة Fast + واحدة Pro)، تمامًا كزر
"Compare Fast vs Pro" في الموقع.

التشغيل:  python examples/04_compare_fast_pro.py
"""
from chatdeep import ChatDeepClient

PROMPT = "لماذا يعتبر الفرز السريع O(n log n) متوسطًا وليس دائمًا؟"

def main() -> None:
    with ChatDeepClient() as client:
        result = client.compare(PROMPT, on_delta=lambda mode, t: None)

        print(f"السؤال: {PROMPT}\n")
        print("=" * 70)
        print("⚡ Fast")
        print("=" * 70)
        print(result.fast.content if result.fast else f"فشل: {result.fast_error}")

        print("\n" + "=" * 70)
        print("🧠 Pro")
        print("=" * 70)
        print(result.pro.content if result.pro else f"فشل: {result.pro_error}")

        if result.fast and result.pro:
            print(f"\nالزمن: Fast {result.fast.total_ms}ms · Pro {result.pro.total_ms}ms"
                  + (f" · استدلال Pro {result.pro.reasoned_ms}ms"
                     if result.pro.reasoned_ms else ""))

if __name__ == "__main__":
    main()
