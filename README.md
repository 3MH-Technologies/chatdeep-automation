<div align="center">

# 🤖 ChatDeep Automation

### أداة أتمتة شات احترافية لـ chat-deep.ai — بلا متصفح، ببايثون خالصة

**تطوير [3MH TECHNOLOGIES](https://3mh.pages.dev/)** — Engineering Intelligent Software
📱 للتواصل والدعم: [Telegram @j49_c](https://t.me/j49_c)

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-43%20passed-brightgreen)](./tests)
[![CI](https://github.com/3MH-Technologies/chatdeep-automation/actions/workflows/tests.yml/badge.svg)](https://github.com/3MH-Technologies/chatdeep-automation/actions/workflows/tests.yml)
[![Browser-less](https://img.shields.io/badge/browser--less-✔-orange)](#-محركات-جلب-توكن-turnstile-كل-الرسائل-تتطلب-توكن)

</div>

---

عميل بايثون كامل + سطر أوامر تفاعلي لأتمتة الشات المدمج في موقع
[chat-deep.ai](https://chat-deep.ai/) عبر **نقاط الـ API الداخلية** الخاصة به
(الموثقة بالكامل في [`docs/API.md`](docs/API.md)) — **بدون اعتماد على متصفح**:
بث SSE حي، وضعَا Fast/Pro، إرفاق الصور، وضع المقارنة، إدارة الحصة اليومية،
وإعادة المحاولة الذكية المطابقة لسلوك عميل الموقع الرسمي.

> ⚖️ **إخلاء مسؤولية:** chat-deep.ai دليل مستقل (غير تابع لـ DeepSeek) يقدم
> شات مجانيًا بحدود (50 رسالة/يوم، منها 10 Pro). الأداة تحترم كل الحدود.
> راجع قسم «محركات التوكن» أدناه لاختيار الأسلوب الأنسب لك والتزامك بشروط
> الموقع وCloudflare.

---

## 🔑 محركات جلب توكن Turnstile (كل الرسائل تتطلب توكن)

| المحرك | متصفح؟ | تكلفة | الأتمتة | الأنسب لـ |
|---|---|---|---|---|
| 🌉 **bridge** (جسر محلي) | متصفحك الشخصي مفتوح على الموقع (بلا أتمتة) | مجاني | كاملة | الاستخدام اليومي المجاني |
| 🛰️ **solver** (CapSolver / 2Captcha / YesCaptcha) | ❌ إطلاقًا | ~$1 لكل ألف حل | كاملة 24/7 | السيرفرات والأتمتة الكاملة |
| ✋ **manual** | متصفحك (نسخ من DevTools) | مجاني | يدوية | التجارب السريعة |
| 🎭 **playwright** (اختياري) | متصفح آلي | مجاني | كاملة | بديل احتياطي |

### 🌉 الخيار المجاني: جسر التوكن (متصفحك يغذي بايثون)

سكربت Tampermonkey صغير يعمل في متصفحك العادي على chat-deep.ai، يشغّل ويدجت
الموقع الرسمي ويحافظ على مخزون توكن طازجة، ويدفعها لخادم محلي (127.0.0.1)
تسحب منه بايثون — **صفر متصفح في بايثون، وصفر تكلفة**:

```bash
# طرفية 1: شغّل الجسر
python -m chatdeep bridge --port 8787 --key mysecret

# لمرة واحدة: ثبّت Tampermonkey، ثم أنشئ سكربتًا جديدًا والصق محتوى:
python -m chatdeep userscript          # يطبع السكربت
python -m chatdeep userscript --save ~/token-bridge.user.js   # أو احفظه ملفًا
# عدّل في أعلى السكربت: bridge/key حسب أمر الجسر، ثم افتح أي صفحة على chat-deep.ai

# طرفية 2: شغّل الأداة
export CHATDEEP_BRIDGE_PORT=8787 CHATDEEP_BRIDGE_KEY=mysecret
python -m chatdeep                     # أو: chatdeep ask "سؤالك"
```

الجسر محلي تمامًا (127.0.0.1)، بمفتاح سري اختياري، ويرفض الطلبات من مواقع
أخرى (فحص Origin). المخزون يُدار بصلاحية 240 ثانية مثل الموقع تمامًا.

### 🛰️ الأتمتة الكاملة بلا أي متصفح: خدمة حل برمجية

```bash
pip install -e .            # المتطلبات الأساسية: requests فقط
export CHATDEEP_SOLVER=capsolver        # أو 2captcha أو yescaptcha
export CHATDEEP_API_KEY=...             # مفتاحك من الخدمة
python -m chatdeep ask "سؤالك" --json
```

| الخدمة | التسجيل | متغيرات البيئة |
|---|---|---|
| CapSolver | capsolver.com | `CHATDEEP_CAPSOLVER_KEY` أو `CAPSOLVER_API_KEY` |
| 2Captcha | 2captcha.com | `CHATDEEP_2CAPTCHA_KEY` أو `TWOCAPTCHA_API_KEY` |
| YesCaptcha | yescaptcha.com | `CHATDEEP_YESCAPTCHA_KEY` |

افحص الرصيد والاتصال مجانًا: `python -m chatdeep doctor`
(يستخدم `getBalance` دون استهلاك أي حل).

> **ملاحظة التزام:** خدمات الحل التجاري قد تخالف شروط Cloudflare/الموقع —
> استخدمها على مسؤوليتك. الخيار الأنظف هو الجسر (متصفحك أنت، ويدجت الموقع
> نفسه، بلا أتمتة للفحص).

### ⚠️ حقيقة «الحلول المجانية» الشائعة (تم التحقق عمليًا — 2026-09-29)

| الادعاء | الحقيقة المُثبتة |
|---|---|
| `pip install cloudflare-turnstile-solver` | **الباقة غير موجودة على PyPI (404)**. الباقات المشابهة (`turnstile-solver`) تقود متصفحًا headless في الباطن، و`pyturnstile` للتحقق من التوكن في سيرفرك أنت وليست للتوليد. أي باقة تدّعي «توليد توكن بخوارزمية محلية» فهي إما وهمية أو ملغومة — التوكن تُصدرها خوادم Cloudflare فقط بعد فحص إشارات متصفح حقيقي |
| توكن الاختبار `1x00000000000000000000AA` تمر دائمًا | **رُفضت عمليًا من chat-deep.ai بـ `403 dsc_turnstile`**. حسب توثيق Cloudflare الرسمي: التوكن الاختبارية تقبلها فقط المفاتيح السرية *الاختبارية* — ومواقع الإنتاج (مثل chat-deep.ai) تتحقق بمفتاحها الإنتاجي فترفضها حتمًا. تصلح لاختبار **موقعك أنت** فقط |
| منصات «حل مجاني 100-500 يوميًا» | الموجود فعليًا: **رصيد تجريبي** لمرة واحدة عند التسجيل (CapSolver/YesCaptcha) يكفي لمئات الرسائل. أما المواقع المجهولة التي تعد بحل مجاني دائم فاحتمال الاحتيال/سرقة المفاتيح منها مرتفع — لا تضع فيها مفتاحًا ولا تعتمد عليها. وتذكر: سقف الموقع نفسه 50 رسالة/يوم لكل زائر، فالجسر المجاني يغطي الحاجة اليومية كاملة |


### متغيرات البيئة (ملخص)

| المتغير | الوظيفة |
|---|---|
| `CHATDEEP_ENGINE` | `auto` (افتراضي) / `solver` / `bridge` / `manual` / `playwright` |
| `CHATDEEP_SOLVER` + `CHATDEEP_API_KEY` | تفعيل محرك solver |
| `CHATDEEP_BRIDGE_PORT` (+`_KEY`, `_EMBED=1`) | تفعيل محرك bridge |
| `CHATDEEP_TURNSTILE_TOKEN` | توكن يدوية واحدة |

ترتيب `auto`: يدوي ← solver ← bridge ← playwright.

---

## ✨ المزايا

| الميزة | التفاصيل |
|---|---|
| 🌊 **بث SSE حي** | أحداث `reasoning` / `delta` / `done` / `error` مع دوال ندائية |
| ⚡🧠 **Fast / Pro** | تبديل الأوضاع + تحويل تلقائي إلى Fast عند نفاد حصة Pro |
| 🆚 **وضع المقارنة** | إرسال واحد ← إجابتان متوازيتان (Fast × Pro) |
| 🖼️ **الصور** | حتى 4 صور/رسالة، إعادة ترميز وتصغير مطابقة لخط أنابيب الموقع (2048px، ≤5MB، تجريد EXIF) |
| 💾 **جلسة دائمة** | حفظ كوكي `dsc_vid` (هوية الحصة) بين الجلسات |
| 📊 **إدارة الحصة** | قراءة يومية/Pro من كل استجابة + منع محلي للتجاوز |
| 🔁 **مرونة** | إعادة nonce/tokens الصامتة، تراجع عند `dsc_rate_limit`، 14 كود خطأ مصنفة باستثناءات typed |
| 🌍 **اكتشاف حي** | يقرأ إعداد `data-cdc` من الصفحة ليتكيف مع تغييرات الموقع |
| 🖥️ **CLI احترافي** | REPL بأوامر مائلة + `ask` + `status` + `doctor` + `bridge` + `userscript` |

---

## 📦 التثبيت

```bash
# الأساس (طلبات: requests فقط!)
pip install -r requirements.txt
pip install -e .                 # اختياري: يوفر الأمر chatdeep

# إضافات اختيارية
pip install pillow               # لإرفاق الصور
pip install playwright && python -m playwright install chromium   # لمحرك playwright فقط

# تحقق شامل من البيئة والمحركات
python -m chatdeep doctor
```

يتطلب Python ≥ 3.9.

---

## 🚀 الاستخدام السريع

### سطر الأوامر

```bash
python -m chatdeep                          # جلسة تفاعلية (REPL)
python -m chatdeep ask "اشرح لي التعلم العميق"
python -m chatdeep ask "حلل" --mode pro --json
python -m chatdeep ask "ما هذا؟" --image photo.png
python -m chatdeep ask "لخص" --compare      # Fast × Pro
python -m chatdeep status                   # حالة الخدمة والحصة
python -m chatdeep bridge --port 8787       # خادم الجسر المحلي
python -m chatdeep userscript               # سكربت متصفح الجسر

# بمحرك محدد صراحةً
python -m chatdeep --solver capsolver --api-key KEY ask "سؤال"
python -m chatdeep --bridge-port 8787 --bridge-key mysecret ask "سؤال"
python -m chatdeep --engine playwright --headed ask "سؤال"
```

### أوامر REPL المائلة

```
/mode pro        تبديل الوضع          /compare on      تفعيل المقارنة
/image shot.png  إرفاق للرسالة التالية /quota           الحصة المتبقية
/status          حالة الخدمة           /engine          المحرك النشط وحالته
/token           اختبار جلب توكن       /system "..."    موجه نظام
/save out.json   تصدير المحادثة        /reset           مسح السياق
/help            المساعدة              /exit            خروج
```

### كمكتبة بايثون

```python
from chatdeep import ChatDeepClient

# المحرك يُختار تلقائيًا من البيئة، أو حدده صراحةً:
client = ChatDeepClient(engine="bridge", bridge_port=8787, bridge_key="mysecret")
# client = ChatDeepClient(engine="solver", solver="capsolver", api_key="KEY")

with client:
    print(client.ask("مرحبًا! عرّف بنفسك"))

    answer = client.chat("اكتب دالة بايثون لفرز قائمة", mode="pro",
                         on_delta=lambda t: print(t, end="", flush=True))

    client.chat("اجعلها تصاعدية")               # سياق متعدد الأدوار تلقائي
    client.chat("صف هذه الصورة", images=["screenshot.png"])

    res = client.compare("ما عاصمة فرنسا؟")     # Fast × Pro
    print(res.fast.content, res.pro.content, sep="\n---\n")

    for ev in client.chat_events("عد من 1 إلى 5"):   # أحداث SSE الخام
        if ev.kind == "delta":
            print(ev.text, end="")

    print(client.quota)          # today 7/50 · pro 2/10 · resets ...
```

### أتمتة مبرمجة مع الجسر المدمج (بلا عمليات خارجية)

```python
from chatdeep import ChatDeepClient, BridgeTokenProvider

# يشغّل خادم الجسر داخل العملية نفسها
provider = BridgeTokenProvider(port=8787, key="s3cret", embed=True)
with ChatDeepClient(token_provider=provider) as c:
    print(c.ask("سؤال"))   # ينتظر حتى يدفع متصفحك توكن (long-poll)
```

---

## 🏗️ بنية المشروع

```
chatdeep-automation/
├── chatdeep/
│   ├── client.py       # ChatDeepClient: الجلسة، الشات، SSE، المقارنة، الحصة
│   ├── providers.py    # مصنع المحركات (auto/solver/bridge/manual/playwright)
│   ├── solvers.py      # CapSolver / 2Captcha / YesCaptcha (createTask/getTaskResult)
│   ├── bridge.py       # الجسر المحلي: خادم HTTP + TokenPool + Provider
│   ├── browser/token-bridge.user.js  # سكربت Tampermonkey المغذي للجسر
│   ├── turnstile.py    # Manual + Playwright providers
│   ├── models.py       # نماذج البيانات (Quota, ChatMessage, StreamEvent, ...)
│   ├── sse.py          # محلل SSE مطابق لمنطق الموقع
│   ├── images.py       # خط أنابيب الصور (مطابق لـ canvas الموقع)
│   ├── errors.py       # 14 كود خطأ ← استثناءات typed
│   └── cli.py          # REPL + ask + status + doctor + bridge + userscript
├── docs/
│   ├── API.md          # ⭐ توثيق نقاط الـ API الداخلية (عربي)
│   └── reference/      # dsc-chat.js الأصلي للاستناد
├── examples/           # 5 أمثلة تشغيلية
├── tests/              # 43 اختبار وحدة (بلا شبكة)
└── scripts/smoke_live.py  # فحص حي شامل
```

## 🔧 كيف تعمل المصادقة؟

لا حساب ولا مفاتيح من الموقع — ثلاث آليات (التفاصيل الكاملة في `docs/API.md`):

1. **كوكي `dsc_vid`** — هوية الزائر وحصة اليوم (تُحفظ في `~/.chatdeep/cookies.json`).
2. **`X-WP-Nonce`** — من نقطة `dsc_session`؛ تُدوَّر تلقائيًا عند `dsc_bad_nonce`.
3. **توكن Turnstile** — لكل رسالة توكن واحدة تُستخدم مرة؛ مصدرها المحرك الذي
   تختاره (جسر متصفحك / خدمة حل / يدوي / playwright).

## 🧪 الاختبارات

```bash
pip install pytest
pytest tests/ -v              # 43 اختبار وحدة: SSE، الجسر، المحركات، الحمولة
python scripts/smoke_live.py  # فحص حي (لا يستهلك رسائل)
```

## 🩺 استكشاف الأخطاء

| العرض | الحل |
|---|---|
| `لا توكن متاحة من الجسر (503)` | تأكد أن تبويب chat-deep.ai مفتوح والسكربت يعمل (افتح Console)، وأن الجسر على نفس المنفذ/المفتاح |
| `createTask فشل: ERROR_KEY...` | مفتاح الخدمة خاطئ — افحصه بـ `doctor` (يعرض الرصيد) |
| `dsc_turnstile_client` (محرك playwright) | عناوين VPS/السحابة يرفضها Cloudflare غالبًا — استخدم solver أو bridge |
| `dsc_day_limit` | انتظر `resets_at` (منتصف الليل UTC) — الحد 50 رسالة/يوم |
| `dsc_rate_limit` | الأداة تتراجع تلقائيًا؛ ارفع `min_interval` إن تكرر |
| `dsc_paused` | الشات المجاني موقوف شهريًا — يعود أول الشهر |
| إجابة `incomplete` | `finish=length`: قسّم سؤالك |

---

## 📤 النشر على GitHub

المستودع مهيأ بالكامل (LICENSE، ‏.gitignore، CI workflow للاختبارات).
أنشئ مستودعًا فارغًا جديدًا على GitHub ثم نفّذ من جذر المشروع:

```bash
git init -b main
git add .
git commit -m "Initial release v1.1.0 — ChatDeep Automation by 3MH TECHNOLOGIES"
git remote add origin https://github.com/3MH-Technologies/chatdeep-automation.git
git push -u origin main
```

> إن كان اسم المستودع/المنظمة مختلفًا، حدّث الروابط في `pyproject.toml`
> (`[project.urls]`) وشارة CI في أعلى README.

---

<div align="center">

## © 2026 [3MH TECHNOLOGIES](https://3mh.pages.dev/)

**Engineering software businesses can build on.**

🌐 [3mh.pages.dev](https://3mh.pages.dev/) · 📱 [t.me/j49_c](https://t.me/j49_c)

الترخيص: [MIT](./LICENSE) — الاستخدام وفق شروط chat-deep.ai وحدوده المعلنة.

</div>
