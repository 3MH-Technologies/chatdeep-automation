# توثيق نقاط الـ API الداخلية لموقع chat-deep.ai

> **إعداد وتطوير:** [3MH TECHNOLOGIES](https://3mh.pages.dev/) · [t.me/j49_c](https://t.me/j49_c) — مرافق لأداة `chatdeep-automation` (ترخيص MIT)
> **الإصدار:** 1.1 — حُرِّر في 29 سبتمبر 2026 (أضيف القسم 8: محركات جلب التوكن بلا متصفح)
> **المصدر:** هندسة عكسية لملف `dsc-chat.js` (إضافة `deepseek-chat` v2.2.0) وملحقات `chatdeep-site-kit` v1.7.0، مع **تحقق حي** من كل نقطة ضد الخادم.
> **تنويه:** chat-deep.ai موقع دليل مستقل يقدم شات متصفح مجانيًا مدعومًا بواجهة DeepSeek API، وليس تابعًا لشركة DeepSeek. هذا التوثيق مخصص لأغراض التشغيل البيني الشخصي والأتمتة المشروعة ضمن حدود الاستخدام المعلنة للموقع.

---

## 1) نظرة عامة

الشات المدمج في الموقع لا يستخدم حسابًا ولا مفتاح API. يعتمد بدلًا من ذلك على ثلاث آليات:

| الآلية | الغرض |
|---|---|
| كوكي `dsc_vid` | هوية الزائر — تُربط بها الحصة اليومية (50 رسالة، منها 10 Pro) |
| ترويسة `X-WP-Nonce` | حماية CSRF — تُسلَّم من نقطة الجلسة وتنتهي صلاحيتها دوريًا |
| توكن Cloudflare Turnstile (`ts`) | فحص أمان **لكل رسالة** — توكن واحدة تُستخدم مرة واحدة، صلاحيتها ~300 ثانية (العميل يجددها عند 240 ثانية) |

كل النقاط تعمل عبر HTTPS على نطاق `chat-deep.ai` وتتطلب كوكيات نفس المنشأ (`credentials: same-origin`).

### اكتشاف الإعدادات ديناميكيًا (data-cdc)

كل صفحة تحتوي الشات تحمل عنصرًا بسمة `data-cdc` تحوي إعداد JSON كامل (نقاط، مفاتيح، حدود). يُستخرج من HTML بعد فك ترميز الكيانات (`&quot;`):

```html
<div class="cdc cdc--embed" id="cdc1" data-cdc="{...}">
```

مثال حي (الصفحة الرئيسية، 2026-09-29):

```json
{
  "variant": "embed",
  "uid": "cdc1",
  "session": "https://chat-deep.ai/wp-admin/admin-ajax.php?action=dsc_session",
  "chat":    "https://chat-deep.ai/wp-json/dsc/v2/chat",
  "notice":  "https://chat-deep.ai/wp-admin/admin-ajax.php?action=dsc_notice",
  "ts":      "0x4AAAAAAE8GhZK3uuXHsQvk",
  "full":    "https://chat-deep.ai/deepseek-chat/",
  "name":    "Chat-Deep Assistant",
  "maxChars": 6000,
  "maxImages": 4,
  "maxImageBytes": 5242880,
  "context": 20,
  "dayLimit": 50,
  "proLimit": 10,
  "voice": 1,
  "privacyMode": "user_choice"
}
```

| الحقل | المعنى |
|---|---|
| `session` | نقطة إنشاء الجلسة (admin-ajax) |
| `chat` | نقطة المحادثة (REST v2، بث SSE) |
| `notice` | نقطة قبول الإشعار الإقليمي |
| `ts` | مفتاح موقع Cloudflare Turnstile |
| `full` | صفحة الشات الكاملة |
| `maxChars` | أقصى طول لرسالة المستخدم (6000 حرف) |
| `maxImages` / `maxImageBytes` | 4 صور، ≤ 5 ميغابايت للصورة بعد الترميز |
| `context` | عدد الرسائل المرسلة كسياق (آخر 20) |
| `dayLimit` / `proLimit` | الحصة اليومية / حصة Pro |
| `privacyMode` | `user_choice` — الإشعار الإقليمي حسب المنطقة |

> عميل بايثون المرفق يقرأ هذا الإعداد حيًّا (`discover_config()`) ويسقط على قيم مدمجة إذا تعذّر ذلك.

---

## 2) نقطة الجلسة — `dsc_session`

```
POST https://chat-deep.ai/wp-admin/admin-ajax.php?action=dsc_session
Content-Type: application/x-www-form-urlencoded

v=2
```

**الاستجابة** `200 application/json` (مثال حي مُتحقَّق):

```json
{
  "nonce": "e3a0447548",
  "access": { "chat": true },
  "paused": false,
  "status": { "state": "ok", "label": "Online" },
  "quota": {
    "day_used": 0,  "day_limit": 50,
    "pro_used": 0,  "pro_limit": 10,
    "day_left": 50, "pro_left": 10,
    "resets_at": "2026-09-29T00:00:00+00:00",
    "unlimited": false
  },
  "consent": { "required": false, "given": true },
  "helpdesk": true
}
```

**أثر جانبي مهم — كوكي الجلسة:** يضبط الخادم `Set-Cookie: dsc_vid=<32 hex>.<8 hex>` (معرّف زائر دائم). **يجب إعادة إرساله في كل الطلبات اللاحقة**، وإلا حُسبت كل رسالة كزائر جديد وقد تُرفض بسبب حدود المعدل.

| الحقل | النوع | المعنى |
|---|---|---|
| `nonce` | string | قيمة ترويسة `X-WP-Nonce` لطلبات chat/notice |
| `access.chat` | bool | هل الشات متاح لهذا الزائر الآن |
| `paused` | bool | الشات موقوف (سقف شهري — يعود أول الشهر) |
| `status.state` | `ok`/`degraded`/`down`/`neutral` | حالة الخدمة للشارة في الواجهة |
| `status.label` | string | نص جاهز للعرض ("Online"…) |
| `quota.*` | int/str/bool | الحصة اليومية — تُحدَّث أيضًا في حدث `done` |
| `consent.required` | bool | الإشعار الإقليمي مطلوب (EEA/UK/CH) |
| `consent.given` | bool | قُبل الإشعار على الخادم |
| `helpdesk` | bool | تفعيل وضع "اسأل عن DeepSeek" |

**متى تُستدعى:** مرة عند الإقلاع، ومرة أخرى عند خطأ `dsc_bad_nonce` (تدوير الـ nonce).

---

## 3) نقطة المحادثة — `dsc/v2/chat` ⭐

```
POST https://chat-deep.ai/wp-json/dsc/v2/chat
Content-Type: application/json
X-WP-Nonce: <nonce من dsc_session>
Cookie: dsc_vid=<...>
```

### جسم الطلب

```json
{
  "messages": [
    { "role": "user", "content": "لخّص هذا النص", "images": ["data:image/jpeg;base64,..."] },
    { "role": "assistant", "content": "..." },
    { "role": "user", "content": "أكمل" }
  ],
  "mode": "fast",
  "helpdesk": false,
  "ts": "<توكن Turnstile — استخدام واحد>",
  "hp": ""
}
```

| الحقل | النوع | التفاصيل |
|---|---|---|
| `messages` | array | آخر **20** رسالة (`user`/`assistant` فقط، بلا رسائل نظام؛ الموقع نفسه لا يرسل system). الصور تُرسل **فقط** مع آخر رسالة مستخدم تحمل صورًا؛ ما قبلها يُستبدل بنص `[N images shared earlier]` |
| `messages[].role` | `user` \| `assistant` | |
| `messages[].content` | string | ≤ 6000 حرف لرسالة المستخدم |
| `messages[].images` | array\<string\> | data URLs (بصيغة `data:image/{jpeg,png,webp};base64,...`) ≤ 4 صور، ≤ 5MiB للصورة. المتصفح يعيد الترميز عبر canvas: أقصى ضلع 2048px، جودة 0.9، ويجرب JPEG q=0.85 بأضلاع ×0.75 حتى يمر الحجم (ويجرد EXIF) |
| `mode` | `fast` \| `pro` | Fast = النموذج السريع؛ Pro = استدلال عميق (حصة 10/يوم) |
| `helpdesk` | bool | وضع "اسأل عن DeepSeek" — يفرض `mode=fast` |
| `ts` | string | توكن Cloudflare Turnstile **طازجة لكل رسالة** (الويدجت الخفي: `action:'chat'`, `execution:'execute'`, `appearance:'interaction-only'`) |
| `hp` | string | **مصيدة بوتات** (حقل `website` المخفي في النموذج) — يجب أن يبقى فارغًا دائمًا؛ أي قيمة = رفض |

### الاستجابة الناجحة — بث SSE

`200` مع `Content-Type: text/event-stream`. الكتل مفصولة بسطر فارغ، وكل كتلة `event:` + `data:` (JSON):

```
event: reasoning
data: {}

event: delta
data: {"t":"مرحبًا"}

event: delta
data: {"t":"! كيف"}

event: done
data: {"finish":"stop","reasoned_ms":4213,"quota":{"day_used":1,"day_limit":50,"pro_used":0,"pro_limit":10,"day_left":49,"pro_left":10,"resets_at":"2026-09-29T00:00:00+00:00","unlimited":false},"paused":false}
```

| الحدث | الحمولة | المعنى |
|---|---|---|
| `reasoning` | `{}` | بدء طور التفكير (نموذج استدلال) |
| `delta` | `{"t": "..."}` | جزء نصي مضاف إلى الإجابة |
| `done` | `{"finish", "reasoned_ms", "quota", "paused"}` | النهاية. `finish`: `stop` (طبيعي) أو `length`/`interrupted` (إجابة غير مكتملة). `quota` المحدَّثة إلزامية المعالجة |
| `error` | `{"code", "message", "quota"?}` | خطأ **أثناء** البث (بعد بدء الاستجابة) |

> **قاعدة التحليل:** احذف `\r`، اقسم على `\n\n`، اجمع أسطر `data:` (بعد القصّ) ثم `JSON.parse`. هذا مطابق لمنطق `readStream` في dsc-chat.js.

### الاستجابة الفاشلة — JSON

عند الرفض قبل البث: حالة HTTP ≠ 200 (أو `Content-Type` ليس event-stream) مع جسم:

```json
{
  "code": "dsc_turnstile",
  "message": "The security check did not pass. Please send your message again.",
  "data": { "status": 403, "quota": { ... } }
}
```

### جدول أكواد الخطأ (كلها مُتحقَّق منها من الكود المصدري)

| الكود | HTTP النموذجي | المعنى | التصرف الموصى به |
|---|---|---|---|
| `dsc_turnstile` | 403 | التوكن مرفوضة/منتهية/مستخدمة | توليد توكن جديدة وإعادة المحاولة **مرة واحدة** |
| `dsc_bad_nonce` | 403 | nonce منتهية | استدعاء `dsc_session` مجددًا وإعادة المحاولة مرة واحدة |
| `dsc_consent` | 403 | الإشعار الإقليمي غير مقبول | `POST dsc_notice` ثم أعد الإرسال |
| `dsc_day_limit` | 429/403 | انتهت حصة اليوم (50) | التوقف حتى `resets_at` (منتصف الليل UTC) |
| `dsc_pro_limit` | 429/403 | انتهت حصة Pro (10) | التحويل إلى `fast` |
| `dsc_rate_limit` | 429 | إرسال سريع جدًا | انتظار مع تراجع أُسّي |
| `dsc_paused` | 503 | الشات موقوف شهريًا | التوقف — يعود أول الشهر |
| `dsc_too_long` | 400 | الرسالة > 6000 حرف | تقصير الرسالة |
| `dsc_bad_image` | 400 | صورة تالفة/كبيرة/صيغة غير مدعومة | إعادة الترميز ≤5MiB (JPEG/PNG/WebP) |
| `dsc_too_many_images` | 400 | أكثر من 4 صور | تقليل العدد |
| `dsc_upstream` | 502/200(SSE) | فشل DeepSeek API خلف الموقع | إعادة المحاولة لاحقًا |
| `dsc_http` | متنوع | خطأ غير مصنف | فحص الاتصال |
| `dsc_session` | — | فشل تهيئة الجلسة (عميل) | إعادة الإقلاع |
| `dsc_turnstile_client` | — | تعذّر توليد التوكن محليًا (عميل) | إعادة تشغيل الويدجت/المتصفح |

---

## 4) نقطة الإشعار الإقليمي — `dsc_notice`

لزوار المنطقة الاقتصادية الأوروبية/بريطانيا/سويسرا (`consent.required=true`):

```
POST https://chat-deep.ai/wp-admin/admin-ajax.php?action=dsc_notice
Content-Type: application/x-www-form-urlencoded
X-WP-Nonce: <nonce>
Cookie: dsc_vid=<...>

ok=1
```

**الاستجابة:** `200` مع `{"given": true}`. بعدها تصبح `consent.given=true` في الجلسة ويُسمح بالشات. بدونها يرفض endpoint المحادثة بكود `dsc_consent`.

---

## 5) نقاط مساعدة (إضافة chatdeep-site-kit)

### 5.1 حالة الخدمة المستقلة — `status.php`

```
GET https://chat-deep.ai/wp-content/plugins/chatdeep-site-kit/status.php
```

استجابة حية مُتحقَّقة:

```json
{
  "ok": true,
  "generated": 1790620141,
  "scope": "one probe location: this site's server",
  "probe": "API gateway reachability and DeepSeek's official status feed, about every 5 minutes",
  "state": "green",
  "checked": 1790619906,
  "latency_ms": 333,
  "success_pct": 100,
  "checks_24h": 290,
  "official": "operational",
  "last_ms": 398,
  "now": 1790620191
}
```

`state`: `green` | `yellow` | `red`. الواجهة تستطلعها كل 60 ثانية (`credentials: omit` — لا تحتاج كوكيز). مفيدة لفحص الصحة قبل الأتمتة.

### 5.2 اكتشاف المنطقة — `region.php`

```
GET https://chat-deep.ai/wp-content/plugins/chatdeep-site-kit/region.php
→ {"region": "row"}      // أو eea | uk | ch
```

تحدد هل سيطلب `dsc_session` إشعار الخصوصية (`consent.required`).

### 5.3 إحصاءات الإعلانات — `wp-json/cdsk/v1/adstat`

```
POST https://chat-deep.ai/wp-json/cdsk/v1/adstat
```

تقرير قياسات داخلي يخص عرض الإعلانات (advanced-ads). **لا علاقة له بسير الشات** ولا تحتاجه الأتمتة؛ ذُكر هنا لاكتمال الجرد.

---

## 6) الحدود والقواعد (ملخص تنفيذي)

| الحد | القيمة | المصدر |
|---|---|---|
| طول رسالة المستخدم | 6000 حرف | `maxChars` + عدّاد الإدخال |
| عدد الصور/رسالة | 4 | `maxImages` |
| حجم الصورة | 5,242,880 بايت | `maxImageBytes` |
| أقصى ضلع بعد التصغير | 2048px | `MAX_EDGE` في dsc-chat.js |
| صيغ الصور المقبولة | JPEG, PNG, GIF, WebP | سمة `accept` في النموذج |
| السياق المُرسل | آخر 20 رسالة | `context` |
| الحصة اليومية | 50 رسالة (تشمل أرجل المقارنة) | `dayLimit` |
| حصة Pro اليومية | 10 رسائل | `proLimit` |
| وضع المقارنة | إرسال واحد = إجابتان = **رسالتان** من الحصة | `requestCompare` |
| صلاحية توكن Turnstile | ~300s (تجديد العميل عند 240s) | `TOKEN_TTL` |
| إعادة المحاولة الصامتة | مرة واحدة لـ bad_nonce و turnstile-403 | `request(mode, retried)` |
| إعادة ضبط الحصة | منتصف الليل UTC | `resets_at` |

---

## 7) التسلسل الكامل لرسالة ناجحة

```
العميل                            chat-deep.ai
  │  GET  / (اختياري: قراءة data-cdc)      │
  │──────────────────────────────────────►│
  │  GET  region.php                       │
  │──────────────────────────────────────►│  {"region":"row"}
  │  POST admin-ajax?action=dsc_session    │
  │        body: v=2                       │
  │──────────────────────────────────────►│  {nonce, quota, ...} + Set-Cookie: dsc_vid
  │  (إن لزم) POST dsc_notice  ok=1        │
  │──────────────────────────────────────►│  {"given":true}
  │  تشغيل ويدجت Turnstile الخفي           │
  │        (challenges.cloudflare.com)     │
  │──────────────────────────────────────►│  token (استخدام واحد)
  │  POST /wp-json/dsc/v2/chat             │
  │    X-WP-Nonce + dsc_vid                │
  │    {messages, mode, ts, hp:""}         │
  │──────────────────────────────────────►│
  │◄═══ SSE: reasoning, delta×N, done ════│  (أو JSON error)
```

**استراتيجيات المرونة المطابقة لعميل الموقع:**
1. `dsc_bad_nonce` ← تحديث الجلسة وإعادة المحاولة مرة واحدة.
2. `dsc_turnstile` مع 403 ← توكن جديدة وإعادة المحاولة مرة واحدة.
3. فشل التوكن محليًا ← إعادة تشغيل الويدجت ثم المحاولة.
4. `dsc_rate_limit` ← تراجع زمني.
5. تحديث `quota` من كل حدث `done`/`error` لتفادي تجاوز الحدود من جهة العميل.

---

## 8) آليات جلب توكن Turnstile (أربعة محركات — ثلاثة منها بلا متصفح في بايثون)

الحقل `ts` إلزامي لكل رسالة. الأداة تدعم أربعة مصادر للتوكن عبر واجهة `TokenProvider` موحدة:

### 8.1 🌉 الجسر المحلي (bridge) — مجاني، متصفحك أنت فقط

خادم HTTP محلي (127.0.0.1) + سكربت Tampermonkey على صفحات chat-deep.ai يشغّل
**ويدجت الموقع الرسمي** في متصفحك الحقيقي ويحافظ على مخزون توكن طازجة.

**بروتوكول الجسر الداخلي:**

```
GET  /health            → {"ok":true}
GET  /status            → {"pool":n, "oldest_age_s":…, "ttl_s":240, "key_required":bool, "stats":{pushed,taken,expired,rejected}}
POST /token  {"token":"0.…"}        → {"ok":true,"pool":n}   (يدفعه سكربت المتصفح)
GET  /token?wait=SECS               → {"token":"0.…","pool":n} (تسحبه بايثون؛ long-poll حتى wait)
                           503      → {"error":"no-token"} عند فراغ المخزون
```

قواعد الأمان: الربط على 127.0.0.1 فقط · مفتاح سري اختياري `X-Bridge-Key` ·
رفض أي طلب يحمل `Origin` خارج `https://chat-deep.ai` (يمنع المواقع الخبيثة من
تغذية/سحب المخزون) · إبطال التوكن الأقدم من 240 ثانية (مطابق لـ `TOKEN_TTL`
في الموقع). السكربت يولّد توكن فقط عند نقص المخزون عن الهدف (افتراضيًا 3)
ويتوقف عندما يكون التبويب مخفياً.

### 8.2 🛰️ خدمات الحل البرمجية (solver) — أتمتة كاملة بلا متصفح

بروتوكول موحّد `createTask`/`getTaskResult` (JSON عبر HTTPS):

```jsonc
// POST {api_base}/createTask
{ "clientKey": "KEY",
  "task": { "type": "<task_type>",
            "websiteURL": "https://chat-deep.ai/",
            "websiteKey": "0x4AAAAAAE8GhZK3uuXHsQvk",
            "metadata": {"action": "chat"} } }        // metadata في CapSolver/YesCaptcha
// → {"errorId":0, "taskId":"…"}

// POST {api_base}/getTaskResult   (استطلاع كل ~3s)
{ "clientKey": "KEY", "taskId": "…" }
// → {"errorId":0, "status":"ready", "solution":{"token":"0.…"}}
//   أو status:"processing" (انتظار) أو status:"failed"
```

| الخدمة | `api_base` | `task_type` |
|---|---|---|
| CapSolver | `https://api.capsolver.com` | `AntiTurnstileTaskProxyLess` |
| 2Captcha | `https://api.2captcha.com` | `TurnstileTaskProxyless` |
| YesCaptcha | `https://api.yescaptcha.com` | `AntiTurnstileTaskProxyLess` |

التشخيص المجاني: `POST {api_base}/getBalance {"clientKey":"KEY"}` → الرصيد دون
استهلاك حلول. **تنبيه التزام:** استخدام خدمات الحل التجاري قد يخالف شروط
Cloudflare/الموقع — على مسؤوليتك.

### 8.3 ✋ يدوي (manual) — للتجارب

انسخ قيمة `ts` من أي طلب شات في DevTools **قبل** إرساله (توكن واحدة = استخدام
واحد ≤ 300 ثانية): `CHATDEEP_TURNSTILE_TOKEN=… chatdeep ask "مرحبا"`.

### 8.4 🎭 متصفح آلي (playwright) — اختياري/احتياطي

يشغّل الويدجت الخفي الرسمي للموقع داخل Chromium (ملف تعريف دائم). **لا يعمل
غالبًا من عناوين VPS/السحابة** لأن Cloudflare يرفضها (600010) — حتى واجهة
الموقع نفسها تفشل من تلك العناوين (تم التحقق عمليًا).

---

## 9) ملاحظات أمنية وأخلاقية

* **مصادر التوكن:** المحرك الافتراضي الموصى به هو الجسر (ويدجت الموقع الرسمي داخل متصفحك الحقيقي — بلا أتمتة للفحص)؛ ومحركات solver خدمات تجارية على مسؤوليتك؛ ولمزيد من التفاصيل راجع القسم 8.
* **احترم الحصص:** الحدود (50/10 يوميًا) موجودة لحماية خدمة مجانية. العميل المرفق يفرضها من جهته أيضًا ويرفض الإرسال عند النفاد.
* **لا ترسل بيانات حساسة:** الموقع يصرّح بأن الرسائل تمر عبر خادمه إلى DeepSeek API.
* **`dsc_vid` = هويتك:** شارك الملف `~/.chatdeep/cookies.json` بحذر؛ من يملكه يملك حصتك.
* هذا التوثيق يصف واجهات **داخلية غير معلنة** قد تتغير دون إشعار؛ لذا يُنصح بالاعتماد على `discover_config()` وعلى أكواد الخطأ الموثقة أعلاه.
