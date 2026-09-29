// ==UserScript==
// @name         ChatDeep Token Bridge — 3MH TECHNOLOGIES
// @namespace    https://3mh.pages.dev/
// @version      1.1.0
// @description  يغذي أداة بايثون (chatdeep) بتوكن Cloudflare Turnstile من متصفحك الحقيقي عبر جسر محلي — بلا Playwright وبلا خدمات مدفوعة
// @author       3MH TECHNOLOGIES (t.me/j49_c)
// @homepageURL  https://3mh.pages.dev/
// @supportURL   https://t.me/j49_c
// @match        https://chat-deep.ai/*
// @connect      127.0.0.1
// @connect      localhost
// @grant        GM_xmlhttpRequest
// @run-at       document-idle
// ==/UserScript==

/* ------------------------------------------------------------------ *
 *  الإعدادات — عدّلها حسب تشغيل `python -m chatdeep bridge`          *
 * ------------------------------------------------------------------ */
const CFG = {
  bridge:   'http://127.0.0.1:8787', // عنوان الجسر المحلي
  key:      '',                      // نفس قيمة --key إن وُجدت
  pool:     3,                       // عدد التوكن الجاهزة المراد إبقاؤها
  pollMs:   4000,                    // فترة فحص حالة الجسر
  sitekey:  '',                      // فارغ = يُقرأ تلقائيًا من data-cdc
  page:     'https://chat-deep.ai/', // صفحة تشغيل الويدجت
  verbose:  true,
};

/* موقع حاوية الويدجت: داخل إطار الرؤية (خارج الشاشة يكسر التحدي 300030)،
   و appearance:'interaction-only' يبقيها غير مرئية عمليًا. */
(function () {
  'use strict';
  const LOG = (...a) => CFG.verbose && console.log('%c[TokenBridge]', 'color:#0a7', ...a);

  const hasGM = typeof GM_xmlhttpRequest === 'function';

  function http(method, url, body) {
    return new Promise((resolve, reject) => {
      if (hasGM) {
        GM_xmlhttpRequest({
          method, url,
          headers: {
            'Content-Type': 'application/json',
            ...(CFG.key ? { 'X-Bridge-Key': CFG.key } : {}),
          },
          data: body ? JSON.stringify(body) : undefined,
          timeout: 120000,
          onload: (r) => {
            try { resolve({ status: r.status, json: JSON.parse(r.responseText || '{}') }); }
            catch (e) { resolve({ status: r.status, json: {} }); }
          },
          onerror: () => reject(new Error('network')),
          ontimeout: () => reject(new Error('timeout')),
        });
      } else {
        fetch(url, {
          method,
          headers: { 'Content-Type': 'application/json', ...(CFG.key ? { 'X-Bridge-Key': CFG.key } : {}) },
          body: body ? JSON.stringify(body) : undefined,
        }).then(async (r) => resolve({ status: r.status, json: await r.json().catch(() => ({})) }))
          .catch(reject);
      }
    });
  }

  function detectSitekey() {
    if (CFG.sitekey) return CFG.sitekey;
    const el = document.querySelector('[data-cdc]');
    if (el) {
      try { return JSON.parse(el.getAttribute('data-cdc')).ts || ''; } catch (e) { /* fallthrough */ }
    }
    return '0x4AAAAAAE8GhZK3uuXHsQvk'; // المفتاح المنشور وقت كتابة السكربت
  }

  function loadTurnstile() {
    return new Promise((resolve, reject) => {
      const t0 = Date.now();
      (function wait() {
        if (window.turnstile && typeof window.turnstile.render === 'function') return resolve(window.turnstile);
        if (Date.now() - t0 > 20000) {
          // الصفحة لم تحمّل api.js — نحقنه بأنفسنا
          const s = document.createElement('script');
          s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
          s.onload = () => {
            const t1 = Date.now();
            (function wait2() {
              if (window.turnstile && typeof window.turnstile.render === 'function') return resolve(window.turnstile);
              if (Date.now() - t1 > 20000) return reject(new Error('turnstile-load'));
              setTimeout(wait2, 150);
            })();
          };
          s.onerror = () => reject(new Error('turnstile-script'));
          document.head.appendChild(s);
          return;
        }
        setTimeout(wait, 150);
      })();
    });
  }

  let widgetId = null;
  let settle = null;

  function runWidget(ts, sitekey) {
    return new Promise((resolve, reject) => {
      let div = document.getElementById('cdpy-bridge-ts');
      if (!div) {
        div = document.createElement('div');
        div.id = 'cdpy-bridge-ts';
        div.style.cssText = 'position:fixed;bottom:12px;left:50%;transform:translateX(-50%);'
          + 'width:302px;min-height:2px;display:flex;justify-content:center;z-index:2147483647;';
        document.body.appendChild(div);
      }
      const timer = setTimeout(() => finish(null, 'timeout'), 90000);
      function finish(token, why) {
        if (!settle) return;
        clearTimeout(timer);
        const done = settle; settle = null;
        token ? done.resolve(token) : done.reject(new Error(why || 'no-token'));
      }
      settle = { resolve, reject };
      try {
        if (widgetId === null) {
          widgetId = ts.render(div, {
            sitekey,
            action: 'chat',
            execution: 'execute',
            appearance: 'interaction-only',
            'response-field': false,
            'refresh-expired': 'never',
            callback: (t) => settle && settle.resolve && finish(t),
            'error-callback': (c) => { finish(null, 'error ' + c); return true; },
            'timeout-callback': () => finish(null, 'interactive timeout'),
          });
        } else {
          ts.reset(widgetId);
        }
        ts.execute(widgetId);
      } catch (e) {
        finish(null, 'render ' + (e && e.message || e));
      }
    });
  }

  async function loop() {
    let ts;
    try { ts = await loadTurnstile(); }
    catch (e) { LOG('تعذّر تحميل Turnstile:', e.message); return setTimeout(loop, 10000); }
    const sitekey = detectSitekey();
    LOG('جاهز — sitekey', sitekey.slice(0, 14) + '…', '· الجسر', CFG.bridge, hasGM ? '(GM)' : '(fetch)');

    let busy = false;
    for (;;) {
      await new Promise((r) => setTimeout(r, CFG.pollMs));
      if (busy || document.hidden) continue;
      let st;
      try { st = (await http('GET', CFG.bridge + '/status')).json; }
      catch (e) { continue; } // الجسر غير مشغل — سنعيد المحاولة
      if (typeof st.pool !== 'number') continue;
      if (st.pool >= CFG.pool) continue;

      busy = true;
      try {
        LOG(`المخزون ${st.pool}/${CFG.pool} — توليد توكن…`);
        const token = await runWidget(ts, sitekey);
        const r = await http('POST', CFG.bridge + '/token', { token });
        if (r.status === 200) LOG('✔ دُفعت توكن — المخزون', r.json.pool);
        else LOG('✖ رفض الجسر الدفع:', r.status, r.json);
      } catch (e) {
        LOG('✖ فشل التوليد:', e.message, '— إن ظهر تحدٍّ تفاعلي فحلّه بالنقر على المربع أسفل الصفحة');
      } finally {
        busy = false;
      }
    }
  }

  loop();
})();
