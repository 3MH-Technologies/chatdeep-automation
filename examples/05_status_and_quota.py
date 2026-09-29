"""مثال 5 — المراقبة: حالة الخدمة، المنطقة، الجلسة، والحصة (بلا رسائل).

مثالي لفحص صحة الموقع قبل تشغيل أتمتة مجدولة.

التشغيل:  python examples/05_status_and_quota.py
"""
import json

from chatdeep import ChatDeepClient

def main() -> None:
    with ChatDeepClient() as client:
        print("— حالة DeepSeek حسب مراقبة chat-deep.ai المستقلة —")
        print(json.dumps(client.service_status(), ensure_ascii=False, indent=2))

        print("\n— المنطقة الجغرافية (تحدد إشعار الخصوصية) —")
        print(client.fetch_region())

        print("\n— الجلسة —")
        info = client.bootstrap()
        print(f"nonce: {info.nonce}")
        print(f"الحالة: {info.status.state} ({info.status.label}) · paused={info.paused}")
        print(f"الوصول للشات: {info.access_chat} · helpdesk={info.helpdesk}")
        print(f"الإشعار الإقليمي مطلوب: {info.consent.required} · مقبول: {info.consent.given}")
        print(f"الحصة: {info.quota}")

if __name__ == "__main__":
    main()
