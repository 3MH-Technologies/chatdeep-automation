"""مثال 3 — تحليل صورة (Vision): إرفاق صورة وسؤال النموذج عنها.

الصور تُعالج محليًا كما يفعل الموقع: تصغير ≤2048px، ضغط ≤5MB، تجريد EXIF،
وترميز data URL. حتى 4 صور في الرسالة الواحدة.

التشغيل:  python examples/03_image_analysis.py path/to/image.png ["سؤالك"]
"""
import sys

from chatdeep import ChatDeepClient

def main() -> None:
    if len(sys.argv) < 2:
        print("الاستخدام: python examples/03_image_analysis.py <image> [question]")
        sys.exit(1)
    image = sys.argv[1]
    question = sys.argv[2] if len(sys.argv) > 2 else "صف هذه الصورة بالتفصيل."

    with ChatDeepClient() as client:
        answer = client.chat(question, images=[image], mode="fast",
                             on_delta=lambda t: (sys.stdout.write(t), sys.stdout.flush()))
        print(f"\n\n[{answer.mode} · {answer.total_ms}ms]")

if __name__ == "__main__":
    main()
