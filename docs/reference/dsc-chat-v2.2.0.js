/*!
 * Chat-Deep Assistant v2 (DeepSeek Chat plugin).
 * Browser-only history (IndexedDB), Fast/Pro modes, image input, streaming.
 * The browser only ever knows the mode names "fast" and "pro".
 * Every message carries a fresh Cloudflare Turnstile token (invisible widget).
 */
(function () {
	'use strict';

	var DB_NAME = 'chatdeep-chat';
	var STORE = 'chats';
	var PREFS_KEY = 'cdc-prefs-v2';
	var NOTICE_KEY = 'cdc-notice-ok-v1';
	var NOTICE_TEXT = 'Your messages are sent to the DeepSeek API, which processes data in China, to generate replies. Nothing is stored on this site.';
	var TOKEN_TTL = 240000; // Turnstile tokens are valid for 300 s; use them well before that.
	var IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
	var MAX_EDGE = 2048;
	var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

	var TEMPLATES = {
		write: 'Write a short, friendly email that ',
		code: 'Write a Python function that ',
		translate: 'Translate the following text into English:\n\n',
		summarize: 'Summarize the following text in five bullet points:\n\n',
		file: 'Analyze this image and explain what it shows.'
	};

	/* ------------------------------------------------------------------ */
	/* Utilities                                                          */
	/* ------------------------------------------------------------------ */

	function qs(sel, root) { return (root || document).querySelector(sel); }
	function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
	function make(tag, cls, text) {
		var node = document.createElement(tag);
		if (cls) { node.className = cls; }
		if (text !== undefined && text !== null) { node.textContent = text; }
		return node;
	}
	function fmt(n) { return Number(n || 0).toLocaleString('en-US'); }
	function newId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }
	function escapeHtml(s) {
		return String(s).replace(/[&<>"']/g, function (c) {
			return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
		});
	}
	function resetTime(iso) {
		var d = new Date(iso);
		if (isNaN(d.getTime())) { return 'midnight UTC'; }
		return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
	}
	function loadPrefs() {
		try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch (e) { return {}; }
	}
	function savePrefs(p) {
		try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch (e) { /* storage disabled */ }
	}
	function dayLabel(ts) {
		var d = new Date(ts); var now = new Date();
		var start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
		if (d.getTime() >= start) { return 'Today'; }
		if (d.getTime() >= start - 86400000) { return 'Yesterday'; }
		return 'Earlier';
	}

	/* ------------------------------------------------------------------ */
	/* Browser-only storage (IndexedDB with an in-memory fallback)        */
	/* ------------------------------------------------------------------ */

	var Store = {
		ready: null,
		memory: {},
		useMemory: false,
		open: function () {
			if (this.ready) { return this.ready; }
			var self = this;
			this.ready = new Promise(function (resolve) {
				if (!window.indexedDB) { self.useMemory = true; resolve(null); return; }
				var req;
				try { req = indexedDB.open(DB_NAME, 1); } catch (e) { self.useMemory = true; resolve(null); return; }
				req.onupgradeneeded = function () {
					var db = req.result;
					if (!db.objectStoreNames.contains(STORE)) {
						db.createObjectStore(STORE, { keyPath: 'id' }).createIndex('updated', 'updated');
					}
				};
				req.onsuccess = function () { resolve(req.result); };
				req.onerror = function () { self.useMemory = true; resolve(null); };
			});
			return this.ready;
		},
		tx: function (mode, fn) {
			var self = this;
			return this.open().then(function (db) {
				if (!db) { return fn(null); }
				return new Promise(function (resolve, reject) {
					var t = db.transaction(STORE, mode);
					var out = fn(t.objectStore(STORE));
					t.oncomplete = function () { resolve(out && out.result !== undefined ? out.result : out); };
					t.onerror = function () { reject(t.error); };
				}).catch(function () { self.useMemory = true; return fn(null); });
			});
		},
		all: function () {
			var self = this;
			return this.tx('readonly', function (s) {
				if (!s) { return { result: Object.keys(self.memory).map(function (k) { return self.memory[k]; }) }; }
				return s.getAll();
			}).then(function (list) {
				return (list || []).sort(function (a, b) { return b.updated - a.updated; });
			});
		},
		get: function (id) {
			var self = this;
			return this.tx('readonly', function (s) { return s ? s.get(id) : { result: self.memory[id] }; });
		},
		put: function (chat) {
			var self = this;
			return this.tx('readwrite', function (s) { if (!s) { self.memory[chat.id] = chat; return null; } return s.put(chat); });
		},
		del: function (id) {
			var self = this;
			return this.tx('readwrite', function (s) { if (!s) { delete self.memory[id]; return null; } return s.delete(id); });
		},
		clear: function () {
			var self = this;
			return this.tx('readwrite', function (s) { if (!s) { self.memory = {}; return null; } return s.clear(); });
		}
	};

	/* ------------------------------------------------------------------ */
	/* Cloudflare Turnstile (loaded only on chat pages, explicit render)  */
	/* ------------------------------------------------------------------ */

	function turnstileReady(timeoutMs) {
		return new Promise(function (resolve, reject) {
			var t0 = Date.now();
			(function poll() {
				if (window.turnstile && typeof window.turnstile.render === 'function') { resolve(window.turnstile); return; }
				if (Date.now() - t0 > timeoutMs) { reject(new Error('turnstile-load')); return; }
				setTimeout(poll, 50);
			})();
		});
	}

	function noticeAccepted() {
		try { return localStorage.getItem(NOTICE_KEY) === '1'; } catch (e) { return false; }
	}
	function rememberNotice() {
		try { localStorage.setItem(NOTICE_KEY, '1'); } catch (e) { /* storage disabled */ }
	}

	/* ------------------------------------------------------------------ */
	/* Markdown (no raw HTML from the model; DOMPurify as a second layer)  */
	/* ------------------------------------------------------------------ */

	var markedReady = false;
	function renderMarkdown(md) {
		if (!window.marked || !window.DOMPurify) { return escapeHtml(md).replace(/\n/g, '<br>'); }
		if (!markedReady) {
			window.marked.use({
				gfm: true,
				breaks: true,
				renderer: {
					html: function (html) { return escapeHtml(typeof html === 'string' ? html : (html && html.text) || ''); }
				}
			});
			window.DOMPurify.addHook('afterSanitizeAttributes', function (node) {
				if (node.tagName === 'A') {
					node.setAttribute('target', '_blank');
					node.setAttribute('rel', 'noopener noreferrer nofollow');
				}
			});
			markedReady = true;
		}
		var html = window.marked.parse(String(md));
		return window.DOMPurify.sanitize(html, {
			ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'input'],
			ALLOWED_ATTR: ['href', 'title', 'class', 'colspan', 'rowspan', 'align', 'start', 'type', 'checked', 'disabled'],
			ALLOW_DATA_ATTR: false
		});
	}

	function copyText(text) {
		if (navigator.clipboard && navigator.clipboard.writeText) {
			return navigator.clipboard.writeText(text);
		}
		return new Promise(function (resolve, reject) {
			var ta = make('textarea');
			ta.value = text;
			ta.setAttribute('readonly', '');
			ta.style.position = 'fixed'; ta.style.opacity = '0';
			document.body.appendChild(ta); ta.select();
			try { document.execCommand('copy'); resolve(); } catch (e) { reject(e); }
			document.body.removeChild(ta);
		});
	}

	function flashButton(btn, label) {
		var old = btn.getAttribute('data-label') || btn.textContent;
		btn.setAttribute('data-label', old);
		btn.textContent = label;
		setTimeout(function () { btn.textContent = old; }, 1600);
	}

	/* ------------------------------------------------------------------ */
	/* Images: re-encode through a canvas (strips EXIF), downscale          */
	/* ------------------------------------------------------------------ */

	function loadBitmap(file) {
		if (window.createImageBitmap) {
			return createImageBitmap(file).catch(function () { return loadImageEl(file); });
		}
		return loadImageEl(file);
	}
	function loadImageEl(file) {
		return new Promise(function (resolve, reject) {
			var url = URL.createObjectURL(file);
			var img = new Image();
			img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
			img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('decode')); };
			img.src = url;
		});
	}
	function encode(bitmap, maxEdge, type, quality) {
		var w = bitmap.width, h = bitmap.height;
		var scale = Math.min(1, maxEdge / Math.max(w, h));
		var canvas = document.createElement('canvas');
		canvas.width = Math.max(1, Math.round(w * scale));
		canvas.height = Math.max(1, Math.round(h * scale));
		var ctx = canvas.getContext('2d');
		if (type === 'image/jpeg') { ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); }
		ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
		return canvas.toDataURL(type, quality);
	}
	function dataBytes(dataUrl) { return Math.floor((dataUrl.length - dataUrl.indexOf(',') - 1) * 3 / 4); }
	function processImage(file, maxBytes) {
		return loadBitmap(file).then(function (bmp) {
			var type = file.type === 'image/jpeg' ? 'image/jpeg' : (file.type === 'image/webp' ? 'image/webp' : 'image/png');
			var data = encode(bmp, MAX_EDGE, type, 0.9);
			var edge = MAX_EDGE;
			while (dataBytes(data) > maxBytes && edge > 512) {
				edge = Math.round(edge * 0.75);
				data = encode(bmp, edge, 'image/jpeg', 0.85);
			}
			var thumb = encode(bmp, 160, 'image/jpeg', 0.7);
			if (bmp.close) { bmp.close(); }
			if (dataBytes(data) > maxBytes) { throw new Error('size'); }
			return { data: data, thumb: thumb, name: file.name || 'image' };
		});
	}

	/* ------------------------------------------------------------------ */
	/* Chat instance                                                      */
	/* ------------------------------------------------------------------ */

	function Chat(root) {
		this.root = root;
		this.cfg = JSON.parse(root.getAttribute('data-cdc'));
		this.full = this.cfg.variant === 'full';
		this.prefs = loadPrefs();
		this.chat = this.blankChat();
		this.attachments = [];
		this.busy = false;
		this.controller = null;
		this.session = null;
		this.sessionJob = null;
		this.quota = null;
		this.tsWidget = null;
		this.tsToken = null;
		this.tsJob = null;
		this.tsSettle = null;
		this.noticeRequired = false;
		this.noticeGiven = true;
		this.noticeEl = null;
		this.helpdesk = false;
		this.compare = false;
		this.helpdeskOk = true;
		this.paused = false;
		this.chatAllowed = true;
		this.limitDay = false;
		this.limitPro = false;
		this.noticeTimer = null;
		this.renderQueued = false;
		this.stickToBottom = true;

		this.el = {
			log: qs('.cdc-log', root),
			empty: qs('.cdc-empty', root),
			input: qs('.cdc-input', root),
			form: qs('.cdc-composer', root),
			send: qs('.cdc-send', root),
			stop: qs('.cdc-stop', root),
			count: qs('.cdc-count', root),
			file: qs('.cdc-file', root),
			attachList: qs('.cdc-attachments', root),
			pill: qs('.cdc-pill', root),
			pillText: qs('.cdc-pill__text', root),
			quota: qs('.cdc-quota', root),
			notice: qs('.cdc-notice', root),
			modes: qsa('.cdc-modes input', root),
			voice: qs('[data-act="voice"]', root),
			hp: qs('.cdc-hp input', root),
			ctxValue: qs('.cdc-context__value', root),
			ctxFill: qs('.cdc-meter__fill', root),
			ts: qs('.cdc-ts', root),
			fullLinks: qsa('.cdc-full', root),
			toggles: qsa('.cdc-toggle', root),
			list: qs('.cdc-list', root),
			side: qs('.cdc-side', root),
			scrim: qs('.cdc-scrim', root),
			openSide: qs('.cdc-side__open', root),
			privacy: qs('[data-act="privacy"]', root),
			theme: qs('[data-act="theme"]', root)
		};
		if (!this.el.ts) {
			this.el.ts = make('div', 'cdc-ts');
			this.el.form.insertBefore(this.el.ts, this.el.form.firstChild);
		}
		this.init();
	}

	Chat.prototype.blankChat = function () {
		var now = Date.now();
		return { id: newId(), title: '', created: now, updated: now, messages: [] };
	};

	Chat.prototype.init = function () {
		var self = this;
		var cfg = this.cfg;

		if (this.cfg.privacyMode === 'never_save') { this.prefs.privacy = true; }
		if (this.cfg.privacyMode === 'always_save') { this.prefs.privacy = false; }
		if (this.el.privacy) { this.el.privacy.checked = !!this.prefs.privacy; }
		if (this.full && this.prefs.theme === 'dark') { this.setTheme('dark'); }
		if (this.prefs.mode === 'pro') { this.setMode('pro', true); }

		this.el.form.addEventListener('submit', function (e) { e.preventDefault(); self.send(); });
		this.el.input.addEventListener('keydown', function (e) {
			if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
				e.preventDefault();
				self.send();
			}
		});
		this.el.input.addEventListener('input', function () { self.onInput(); self.prewarm(); });
		this.el.input.addEventListener('focus', function () { self.prewarm(); });
		this.el.input.addEventListener('paste', function (e) {
			var files = [];
			var items = (e.clipboardData && e.clipboardData.items) || [];
			for (var i = 0; i < items.length; i++) {
				if (items[i].kind === 'file') { var f = items[i].getAsFile(); if (f) { files.push(f); } }
			}
			if (files.length) { e.preventDefault(); self.addFiles(files); }
		});
		this.el.form.addEventListener('dragover', function (e) { e.preventDefault(); self.el.form.classList.add('is-drop'); });
		this.el.form.addEventListener('dragleave', function () { self.el.form.classList.remove('is-drop'); });
		this.el.form.addEventListener('drop', function (e) {
			e.preventDefault();
			self.el.form.classList.remove('is-drop');
			if (e.dataTransfer && e.dataTransfer.files) { self.addFiles(Array.prototype.slice.call(e.dataTransfer.files)); }
		});
		this.el.file.addEventListener('change', function () {
			self.addFiles(Array.prototype.slice.call(self.el.file.files || []));
			self.el.file.value = '';
		});
		this.el.modes.forEach(function (r) {
			r.addEventListener('change', function () { if (r.checked) { self.setMode(r.value); } });
		});
		this.el.log.addEventListener('scroll', function () {
			var l = self.el.log;
			self.stickToBottom = l.scrollHeight - l.scrollTop - l.clientHeight < 60;
		});

		this.root.addEventListener('click', function (e) {
			var btn = e.target.closest('[data-act], [data-chip]');
			if (!btn || !self.root.contains(btn)) { return; }
			if (btn.hasAttribute('data-chip')) { self.useTemplate(btn.getAttribute('data-chip')); return; }
			self.action(btn.getAttribute('data-act'), btn, e);
		});
		if (this.el.privacy) {
			this.el.privacy.addEventListener('change', function () { self.setPrivacy(self.el.privacy.checked); });
		}
		document.addEventListener('keydown', function (e) {
			if (e.key === 'Escape' && self.root.classList.contains('is-side-open')) { self.toggleSide(false); }
		});

		this.basePlaceholder = this.el.input.placeholder;
		this.setupVoice();
		this.onInput();
		this.updateContext();

		Store.open().then(function () {
			var wanted = null;
			if (self.full) {
				try { wanted = new URLSearchParams(window.location.search).get('c'); } catch (e) { wanted = null; }
			}
			return (wanted ? Store.get(wanted) : Promise.resolve(null)).then(function (chat) {
				if (chat && chat.messages) { self.chat = chat; self.renderAll(); }
				if (self.full) { self.renderList(); }
			});
		});

		this.ensureSession();
	};

	/* ---------------- session, availability, quota ---------------- */

	// One session request at a time; every caller waits on the same promise.
	// Resolves with the session, or null when it could not be loaded.
	Chat.prototype.ensureSession = function (refresh) {
		var self = this;
		if (refresh) { this.session = null; }
		if (this.session) { return Promise.resolve(this.session); }
		if (!this.sessionJob) {
			this.sessionJob = this.fetchSession().then(function (s) { self.sessionJob = null; return s || null; });
		}
		return this.sessionJob;
	};

	Chat.prototype.fetchSession = function () {
		var self = this;
		return fetch(this.cfg.session, {
			method: 'POST',
			credentials: 'same-origin',
			cache: 'no-store',
			headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
			body: 'v=2'
		}).then(function (r) {
			if (!r.ok) { throw new Error('session ' + r.status); }
			return r.json();
		}).then(function (s) {
			self.session = s;
			self.paused = !!s.paused;
			self.chatAllowed = !!(s.access && s.access.chat);
			self.setPill(s.status ? s.status.state : 'neutral', s.status ? s.status.label : 'Available');
			self.applyQuota(s.quota);
			self.applyConsent(s.consent);
			self.helpdeskOk = s.helpdesk !== false;
			self.syncToggles();
			self.applyAvailability();
			return s;
		}).catch(function () {
			self.setPill('neutral', 'Status unknown');
			self.showNotice('The chat could not connect. Check your connection and reload the page.', 'warn');
			return null;
		});
	};

	/* ---------------- regional notice (EEA, UK, Switzerland) ---------------- */

	Chat.prototype.applyConsent = function (c) {
		this.noticeRequired = !!(c && c.required);
		this.noticeGiven = !this.noticeRequired || !!c.given;
		if (this.noticeRequired && this.noticeGiven) { rememberNotice(); }
		if (this.noticeRequired && !this.noticeGiven && noticeAccepted()) {
			// Accepted earlier in this browser: restore it on the server quietly.
			this.acceptNotice(true);
		}
		this.renderNotice();
		this.updateSendState();
	};

	Chat.prototype.renderNotice = function () {
		var show = this.noticeRequired && !this.noticeGiven;
		if (!show) {
			if (this.noticeEl) { this.noticeEl.hidden = true; }
			return;
		}
		if (!this.noticeEl) {
			var box = make('div', 'cdc-consent');
			box.setAttribute('role', 'region');
			box.setAttribute('aria-label', 'Before you start');
			box.appendChild(make('p', 'cdc-consent__text', NOTICE_TEXT));
			var ok = make('button', 'cdc-btn cdc-btn--primary cdc-consent__ok', 'I understand, continue');
			ok.type = 'button';
			ok.setAttribute('data-act', 'notice-ok');
			box.appendChild(ok);
			this.el.form.parentNode.insertBefore(box, this.el.form);
			this.noticeEl = box;
		}
		this.noticeEl.hidden = false;
	};

	Chat.prototype.acceptNotice = function (quiet) {
		var self = this;
		var btn = this.noticeEl ? qs('.cdc-consent__ok', this.noticeEl) : null;
		if (btn) { btn.disabled = true; }
		return this.ensureSession().then(function (s) {
			if (!s) { throw new Error('session'); }
			return fetch(self.cfg.notice, {
				method: 'POST',
				credentials: 'same-origin',
				cache: 'no-store',
				headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-WP-Nonce': s.nonce },
				body: 'ok=1'
			});
		}).then(function (r) {
			if (!r.ok) { throw new Error('notice ' + r.status); }
			return r.json();
		}).then(function (j) {
			if (!j || !j.given) { throw new Error('notice'); }
			self.noticeGiven = true;
			rememberNotice();
			self.renderNotice();
			self.updateSendState();
			if (!quiet) { self.el.input.focus(); }
		}).catch(function () {
			if (!quiet) { self.showNotice('Your choice could not be saved. Check your connection and try again.', 'warn'); }
		}).then(function () {
			if (btn) { btn.disabled = false; }
		});
	};

	/* ---------------- Turnstile token (fresh for every send) ---------------- */

	// Runs the invisible widget once and resolves with a single-use token.
	Chat.prototype.runTurnstile = function () {
		var self = this;
		return turnstileReady(20000).then(function (ts) {
			return new Promise(function (resolve, reject) {
				var done = false;
				var timer = setTimeout(function () { finish(null, 'timeout'); }, 60000);
				function finish(token, why) {
					if (done) { return; }
					done = true;
					clearTimeout(timer);
					self.tsSettle = null;
					self.root.classList.remove('is-verifying');
					if (token) { resolve(token); } else { reject({ code: 'dsc_turnstile_client', message: 'The security check did not finish. Please try again.', why: why }); }
				}
				self.tsSettle = finish;
				if (self.tsWidget === null) {
					self.tsWidget = ts.render(self.el.ts, {
						sitekey: self.cfg.ts,
						action: 'chat',
						execution: 'execute',
						appearance: 'interaction-only',
						'response-field': false,
						'refresh-expired': 'never',
						callback: function (token) { if (self.tsSettle) { self.tsSettle(token); } },
						'error-callback': function (code) { if (self.tsSettle) { self.tsSettle(null, 'error ' + code); } return true; },
						'timeout-callback': function () { if (self.tsSettle) { self.tsSettle(null, 'interactive timeout'); } },
						'expired-callback': function () { self.tsToken = null; },
						'before-interactive-callback': function () { self.root.classList.add('is-verifying'); },
						'after-interactive-callback': function () { self.root.classList.remove('is-verifying'); }
					});
				} else {
					ts.reset(self.tsWidget);
				}
				ts.execute(self.tsWidget);
			});
		}, function () {
			throw { code: 'dsc_turnstile_client', message: 'The security check could not load. Check your connection or allow challenges.cloudflare.com, then try again.' };
		});
	};

	// Make sure a fresh token is ready (one widget run at a time).
	Chat.prototype.ensureToken = function () {
		var self = this;
		if (this.tsToken && Date.now() - this.tsToken.at < TOKEN_TTL) { return Promise.resolve(); }
		this.tsToken = null;
		if (!this.tsJob) {
			var clear = function () { self.tsJob = null; };
			this.tsJob = this.runTurnstile().then(function (v) { self.tsToken = { v: v, at: Date.now() }; clear(); }, function (e) { clear(); throw e; });
		}
		return this.tsJob;
	};

	// Take the ready token (single use) or run the widget now.
	Chat.prototype.takeToken = function () {
		var self = this;
		if (!this.cfg.ts) { return Promise.reject({ code: 'dsc_turnstile_client', message: 'The chat is temporarily unavailable. Please try again later.' }); }
		return this.ensureToken().then(function () {
			var tok = self.tsToken;
			self.tsToken = null;
			if (tok && Date.now() - tok.at < TOKEN_TTL) { return tok.v; }
			return self.ensureToken().then(function () { var t2 = self.tsToken; self.tsToken = null; return t2.v; });
		});
	};

	// Start the check while the visitor is typing so Send does not wait for it.
	Chat.prototype.prewarm = function () {
		if (!this.cfg.ts || this.busy || this.tsJob || this.tsToken) { return; }
		if (this.noticeRequired && !this.noticeGiven) { return; }
		this.ensureToken().catch(function () { /* retried on send */ });
	};

	Chat.prototype.setPill = function (state, label) {
		this.el.pill.setAttribute('data-state', state);
		this.el.pillText.textContent = label;
	};

	Chat.prototype.applyAvailability = function () {
		if (this.paused) {
			this.showNotice('The free chat has reached this month\'s limit. It reopens on the 1st.', 'warn', true);
		} else if (!this.chatAllowed) {
			this.showNotice('The chat is temporarily unavailable. Please try again later.', 'warn', true);
		}
		this.updateSendState();
	};

	Chat.prototype.applyQuota = function (q) {
		if (!q) { return; }
		this.quota = q;
		var text = q.unlimited
			? 'Today: unlimited (admin)'
			: 'Today ' + fmt(q.day_used) + ' / ' + fmt(q.day_limit) + ' Â· Pro ' + fmt(q.pro_used) + ' / ' + fmt(q.pro_limit);
		this.el.quota.textContent = text;
		this.limitDay = !q.unlimited && q.day_left <= 0;
		this.limitPro = !q.unlimited && q.pro_left <= 0;
		this.el.quota.classList.toggle('is-limit', this.limitDay || this.limitPro);
		// Compare needs one Fast and one Pro message.
		this.compareBlocked = !q.unlimited && (q.pro_left <= 0 || q.day_left < 2);
		if (this.compareBlocked && this.compare) { this.setToggle('compare', false); }
		this.syncToggles();

		var proInput = this.modeInput('pro');
		if (proInput) {
			proInput.disabled = this.limitPro;
			proInput.parentNode.classList.toggle('is-disabled', this.limitPro);
			proInput.parentNode.title = this.limitPro ? 'Pro limit reached for today. Resets at ' + resetTime(q.resets_at) + '.' : 'Reasons before answering';
		}
		if (this.limitDay) {
			this.showNotice('You have used all ' + fmt(q.day_limit) + ' messages for today. The limit resets at ' + resetTime(q.resets_at) + '.', 'warn', true);
		} else if (this.limitPro && this.mode() === 'pro') {
			this.setMode('fast');
			this.showNotice('You have used all ' + fmt(q.pro_limit) + ' Pro messages for today. Fast mode is still available. Pro resets at ' + resetTime(q.resets_at) + '.', 'warn', true);
		} else if (!this.paused && this.chatAllowed && this.el.notice.getAttribute('data-sticky') === '1') {
			this.hideNotice();
		}
		this.updateSendState();
	};

	Chat.prototype.showNotice = function (text, tone, sticky) {
		var n = this.el.notice;
		n.textContent = text;
		n.setAttribute('data-tone', tone || 'info');
		n.setAttribute('data-sticky', sticky ? '1' : '0');
		n.hidden = false;
		clearTimeout(this.noticeTimer);
		if (!sticky) {
			var self = this;
			this.noticeTimer = setTimeout(function () { self.hideNotice(); }, 8000);
		}
	};
	Chat.prototype.hideNotice = function () { this.el.notice.hidden = true; this.el.notice.textContent = ''; };

	/* ---------------- mode, theme, privacy, side panel ---------------- */

	Chat.prototype.modeInput = function (value) {
		return this.el.modes.filter(function (r) { return r.value === value; })[0];
	};
	Chat.prototype.mode = function () {
		var r = this.el.modes.filter(function (x) { return x.checked; })[0];
		return r ? r.value : 'fast';
	};
	Chat.prototype.setMode = function (value, silent) {
		var input = this.modeInput(value);
		if (!input || input.disabled) { return; }
		input.checked = true;
		this.root.setAttribute('data-mode', value);
		this.prefs.mode = value;
		if (!silent) { savePrefs(this.prefs); }
	};
	Chat.prototype.setTheme = function (theme) {
		this.root.setAttribute('data-theme', theme);
		if (this.el.theme) { this.el.theme.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false'); }
		this.prefs.theme = theme;
		savePrefs(this.prefs);
	};
	Chat.prototype.setPrivacy = function (on) {
		this.prefs.privacy = !!on;
		savePrefs(this.prefs);
		if (on) {
			var self = this;
			Store.del(this.chat.id).then(function () { self.renderList(); });
			this.showNotice('Privacy mode is on: new messages are not saved in this browser.', 'info');
		} else {
			this.persist();
			this.showNotice('Privacy mode is off: this conversation is saved in this browser only.', 'info');
		}
	};
	Chat.prototype.toggleSide = function (open) {
		if (!this.full) { return; }
		this.root.classList.toggle('is-side-open', open);
		if (this.el.scrim) { this.el.scrim.hidden = !open; }
		if (this.el.openSide) { this.el.openSide.setAttribute('aria-expanded', open ? 'true' : 'false'); }
		if (open) {
			var first = qs('.cdc-side button, .cdc-side a', this.root);
			if (first) { first.focus(); }
		} else if (this.el.openSide) {
			this.el.openSide.focus();
		}
	};

	/* ---------------- special modes: helpdesk and compare ---------------- */

	Chat.prototype.setToggle = function (name, on) {
		if (name === 'compare' && on && this.compareBlocked) { return; }
		if (name === 'helpdesk' && on && !this.helpdeskOk) { return; }
		this[name] = !!on;
		// Helpdesk always answers in Fast mode, so the two cannot be combined.
		if (on) { this[name === 'helpdesk' ? 'compare' : 'helpdesk'] = false; }
		this.syncToggles();
		this.el.input.placeholder = this.helpdesk ? 'Ask about DeepSeek: login, app, pricing, modelsâ¦' : (this.compare ? 'One message, answered by Fast and Proâ¦' : this.basePlaceholder);
		this.el.input.focus();
	};

	Chat.prototype.syncToggles = function () {
		var self = this;
		this.root.classList.toggle('is-helpdesk', this.helpdesk);
		this.root.classList.toggle('is-compare', this.compare);
		(this.el.toggles || []).forEach(function (b) {
			var name = b.getAttribute('data-act');
			b.setAttribute('aria-pressed', self[name] ? 'true' : 'false');
			if (name === 'compare') {
				b.disabled = !!self.compareBlocked;
				b.title = self.compareBlocked ? 'Compare needs one Fast and one Pro message, and today\'s allowance is used up.' : '';
			}
			if (name === 'helpdesk') { b.disabled = !self.helpdeskOk; }
		});
	};

	/* ---------------- actions ---------------- */

	Chat.prototype.action = function (act, btn, e) {
		var self = this;
		switch (act) {
			case 'attach': this.el.file.click(); break;
			case 'stop': this.stop(); break;
			case 'new': this.newChat(); this.toggleSide(false); break;
			case 'open-side': this.toggleSide(true); break;
			case 'close-side': this.toggleSide(false); break;
			case 'theme': this.setTheme(this.root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'); break;
			case 'voice': this.toggleVoice(); break;
			case 'helpdesk': this.setToggle('helpdesk', !this.helpdesk); break;
			case 'compare': this.setToggle('compare', !this.compare); break;
			case 'notice-ok': this.acceptNotice(false); break;
			case 'clear-all':
				if (window.confirm('Delete all saved conversations in this browser? This cannot be undone.')) {
					Store.clear().then(function () { self.newChat(); self.renderList(); });
				}
				break;
			case 'copy-md':
				copyText(this.exportText('md')).then(function () { flashButton(btn, 'Copied'); }, function () { flashButton(btn, 'Copy failed'); });
				break;
			case 'copy-txt':
				copyText(this.exportText('txt')).then(function () { flashButton(btn, 'Copied'); }, function () { flashButton(btn, 'Copy failed'); });
				break;
			case 'dl-md':
			case 'dl-txt':
				this.prepareDownload(btn, act === 'dl-md' ? 'md' : 'txt');
				break;
			case 'open-chat': this.openChat(btn.getAttribute('data-id')); this.toggleSide(false); break;
			case 'rename-chat': this.startRename(btn.getAttribute('data-id')); break;
			case 'delete-chat':
				if (window.confirm('Delete this conversation?')) {
					var id = btn.getAttribute('data-id');
					Store.del(id).then(function () { if (id === self.chat.id) { self.newChat(); } self.renderList(); });
				}
				break;
			case 'copy-answer': copyText(btn.closest('.cdc-msg').getAttribute('data-raw') || '').then(function () { flashButton(btn, 'Copied'); }); break;
			case 'copy-code': copyText(btn.closest('.cdc-code').querySelector('code').textContent).then(function () { flashButton(btn, 'Copied'); }); break;
			case 'regenerate': this.regenerate(); break;
			case 'retry': this.regenerate(); break;
			case 'remove-image':
				this.attachments.splice(Number(btn.getAttribute('data-index')), 1);
				this.renderAttachments();
				this.el.input.focus();
				break;
		}
		if (e && btn.tagName === 'A' && act.indexOf('dl-') !== 0) { e.preventDefault(); }
	};

	Chat.prototype.useTemplate = function (key) {
		var text = TEMPLATES[key] || '';
		if (key === 'file') {
			if (!this.el.input.value.trim()) { this.el.input.value = text; }
			this.onInput();
			this.el.file.click();
			return;
		}
		this.el.input.value = text + (this.el.input.value && this.el.input.value !== text ? this.el.input.value : '');
		this.onInput();
		this.el.input.focus();
		var end = this.el.input.value.length;
		this.el.input.setSelectionRange(end, end);
	};

	/* ---------------- composer ---------------- */

	Chat.prototype.onInput = function () {
		var ta = this.el.input;
		var max = this.full ? 220 : 150;
		ta.style.height = 'auto';
		ta.style.height = Math.min(ta.scrollHeight, max) + 'px';
		var n = ta.value.length;
		this.el.count.textContent = fmt(n) + ' / ' + fmt(this.cfg.maxChars);
		this.el.count.classList.toggle('is-near', n > this.cfg.maxChars * 0.9);
		this.updateSendState();
	};

	Chat.prototype.canSend = function () {
		return !this.busy && !this.paused && this.chatAllowed && !this.limitDay &&
			!(this.noticeRequired && !this.noticeGiven) &&
			(this.el.input.value.trim().length > 0 || this.attachments.length > 0) &&
			this.el.input.value.length <= this.cfg.maxChars;
	};
	Chat.prototype.updateSendState = function () {
		this.el.send.disabled = !this.canSend();
		this.el.send.hidden = this.busy;
		this.el.stop.hidden = !this.busy;
	};

	Chat.prototype.addFiles = function (files) {
		var self = this;
		var room = this.cfg.maxImages - this.attachments.length;
		if (!files.length) { return; }
		if (room <= 0) { this.showNotice('You can attach up to ' + this.cfg.maxImages + ' images per message.', 'warn'); return; }
		var accepted = [];
		files.forEach(function (f) {
			if (IMAGE_TYPES.indexOf(f.type) === -1) { self.showNotice('Only JPEG, PNG, GIF, and WebP images are supported.', 'warn'); return; }
			if (f.size > self.cfg.maxImageBytes) { self.showNotice('â' + f.name + 'â is larger than 5 MB.', 'warn'); return; }
			accepted.push(f);
		});
		if (accepted.length > room) {
			this.showNotice('Only ' + room + ' more image' + (room === 1 ? '' : 's') + ' can be added to this message.', 'warn');
			accepted = accepted.slice(0, room);
		}
		accepted.forEach(function (f) {
			var slot = { pending: true, name: f.name };
			self.attachments.push(slot);
			self.renderAttachments();
			processImage(f, self.cfg.maxImageBytes).then(function (img) {
				slot.pending = false; slot.data = img.data; slot.thumb = img.thumb;
				self.renderAttachments();
			}).catch(function () {
				self.attachments.splice(self.attachments.indexOf(slot), 1);
				self.renderAttachments();
				self.showNotice('â' + f.name + 'â could not be read as an image.', 'warn');
			});
		});
	};

	Chat.prototype.renderAttachments = function () {
		var list = this.el.attachList;
		list.innerHTML = '';
		list.hidden = this.attachments.length === 0;
		var self = this;
		this.attachments.forEach(function (a, i) {
			var li = make('li', 'cdc-att' + (a.pending ? ' is-pending' : ''));
			if (a.thumb) {
				var img = make('img'); img.src = a.thumb; img.alt = ''; img.width = 48; img.height = 48;
				li.appendChild(img);
			} else {
				li.appendChild(make('span', 'cdc-att__spin', ''));
			}
			var rm = make('button', 'cdc-att__remove', 'Ã');
			rm.type = 'button';
			rm.setAttribute('data-act', 'remove-image');
			rm.setAttribute('data-index', String(i));
			rm.setAttribute('aria-label', 'Remove ' + (a.name || 'image'));
			li.appendChild(rm);
			list.appendChild(li);
		});
		this.updateSendState();
		if (self.attachments.some(function (a) { return a.pending; })) { this.el.send.disabled = true; }
	};

	/* ---------------- voice (browser speech-to-text) ---------------- */

	Chat.prototype.setupVoice = function () {
		var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
		if (!this.el.voice || !this.cfg.voice || !SR) { return; }
		var self = this;
		this.el.voice.hidden = false;
		this.recognition = new SR();
		this.recognition.lang = document.documentElement.lang || 'en-US';
		this.recognition.interimResults = false;
		this.recognition.onresult = function (ev) {
			var text = '';
			for (var i = ev.resultIndex; i < ev.results.length; i++) { text += ev.results[i][0].transcript; }
			self.el.input.value = (self.el.input.value ? self.el.input.value + ' ' : '') + text.trim();
			self.onInput();
		};
		this.recognition.onend = function () { self.el.voice.setAttribute('aria-pressed', 'false'); self.listening = false; };
	};
	Chat.prototype.toggleVoice = function () {
		if (!this.recognition) { return; }
		if (this.listening) { this.recognition.stop(); return; }
		try { this.recognition.start(); this.listening = true; this.el.voice.setAttribute('aria-pressed', 'true'); } catch (e) { /* already running */ }
	};

	/* ---------------- sending ---------------- */

	Chat.prototype.send = function () {
		var self = this;
		if (!this.canSend() || this.attachments.some(function (a) { return a.pending; })) { return; }
		if (!this.session) {
			// Sent before the session arrived: wait for it (availability and the
			// regional notice decide whether this message may go out).
			this.el.send.disabled = true;
			this.ensureSession().then(function (s) { self.updateSendState(); if (s) { self.send(); } });
			return;
		}
		var text = this.el.input.value.trim();
		var images = this.attachments.map(function (a) { return { data: a.data, thumb: a.thumb }; });
		var msg = { role: 'user', content: text, images: images, ts: Date.now() };
		this.chat.messages.push(msg);
		if (!this.chat.title) { this.chat.title = (text || 'Image').replace(/\s+/g, ' ').slice(0, 60); }
		this.el.input.value = '';
		this.attachments = [];
		this.renderAttachments();
		this.onInput();
		this.appendMessage(msg, this.chat.messages.length - 1);
		this.stickToBottom = true;
		this.scrollToBottom();
		this.persist();
		if (this.compare && !this.compareBlocked) { this.requestCompare(); } else { this.request(this.mode()); }
	};

	Chat.prototype.regenerate = function () {
		if (this.busy) { return; }
		var msgs = this.chat.messages;
		while (msgs.length && msgs[msgs.length - 1].role !== 'user') { msgs.pop(); }
		if (!msgs.length) { return; }
		this.renderAll();
		this.request(this.mode());
	};

	// Last `context` messages; images are re-sent only for the most recent
	// user message that has them.
	Chat.prototype.payloadMessages = function () {
		var ctx = this.cfg.context;
		var list = this.chat.messages.filter(function (m) { return (m.role === 'user' || m.role === 'assistant') && !m.error; }).slice(-ctx);
		var lastImageIndex = -1;
		for (var i = list.length - 1; i >= 0; i--) {
			if (list[i].role === 'user' && list[i].images && list[i].images.length) { lastImageIndex = i; break; }
		}
		return list.map(function (m, idx) {
			var out = { role: m.role, content: m.content || '' };
			if (m.role === 'user' && m.images && m.images.length) {
				if (idx === lastImageIndex && m.images.every(function (im) { return !!im.data; })) {
					out.images = m.images.map(function (im) { return im.data; });
				} else {
					out.content = (out.content ? out.content + '\n\n' : '') + '[' + m.images.length + ' image' + (m.images.length === 1 ? '' : 's') + ' shared earlier]';
				}
			}
			return out;
		});
	};

	Chat.prototype.request = function (mode, retried) {
		var self = this;
		var messages = this.payloadMessages();
		var helpdesk = !!this.helpdesk;
		var answer = { role: 'assistant', content: '', ts: Date.now(), mode: helpdesk ? 'fast' : mode };
		if (helpdesk) { answer.helpdesk = true; }
		var bubble = this.appendMessage(answer, -1, true);
		var reasonStart = 0;

		this.busy = true;
		this.updateSendState();
		this.controller = new AbortController();
		this.currentAnswer = answer;
		this.currentBubble = bubble;

		var signal = this.controller.signal;
		this.ensureSession().then(function (s) {
			if (!s) { throw { code: 'dsc_session', message: 'The chat could not connect. Check your connection and try again.' }; }
			return self.takeToken();
		}).then(function (token) {
			if (signal.aborted) { throw { name: 'AbortError' }; }
			return fetch(self.cfg.chat, {
				method: 'POST',
				credentials: 'same-origin',
				cache: 'no-store',
				headers: { 'Content-Type': 'application/json', 'X-WP-Nonce': self.session ? self.session.nonce : '' },
				body: JSON.stringify({ messages: messages, mode: helpdesk ? 'fast' : mode, helpdesk: helpdesk, ts: token, hp: self.el.hp ? self.el.hp.value : '' }),
				signal: signal
			});
		}).then(function (res) {
			var type = res.headers.get('Content-Type') || '';
			if (!res.ok || type.indexOf('text/event-stream') === -1) {
				return res.json().catch(function () { return {}; }).then(function (j) {
					throw { code: j.code || 'dsc_http', message: j.message || 'Something went wrong. Please try again.', data: j.data || {}, status: res.status };
				});
			}
			return self.readStream(res, {
				reasoning: function () {
					reasonStart = Date.now();
					self.setStatus(bubble, 'Reasoningâ¦', true);
				},
				delta: function (t) {
					if (reasonStart && !answer.reasoned_ms) {
						answer.reasoned_ms = Date.now() - reasonStart;
						self.setStatus(bubble, 'Reasoned for ' + Math.max(1, Math.round(answer.reasoned_ms / 1000)) + ' s', false);
					}
					answer.content += t;
					self.queueRender(bubble, answer);
				},
				done: function (d) {
					if (d.reasoned_ms && reasonStart) {
						answer.reasoned_ms = d.reasoned_ms;
						self.setStatus(bubble, 'Reasoned for ' + Math.max(1, Math.round(d.reasoned_ms / 1000)) + ' s', false);
					}
					if (d.finish === 'length' || d.finish === 'interrupted') { answer.incomplete = true; }
					if (d.quota) { self.applyQuota(d.quota); }
					if (d.paused) { self.paused = true; self.applyAvailability(); }
				},
				error: function (d) {
					if (d.quota) { self.applyQuota(d.quota); }
					throw { code: d.code || 'dsc_upstream', message: d.message, data: d, streamed: true };
				}
			});
		}).then(function () {
			self.finishAnswer(bubble, answer, false);
		}).catch(function (err) {
			if (err && err.name === 'AbortError') {
				answer.incomplete = true;
				self.finishAnswer(bubble, answer, true);
				self.ensureSession(true);
				return;
			}
			var e = err || {};
			var data = e.data || {};
			if (data.quota) { self.applyQuota(data.quota); }
			if (e.code === 'dsc_consent') {
				self.noticeGiven = false;
				try { localStorage.removeItem(NOTICE_KEY); } catch (x) { /* storage disabled */ }
				self.renderNotice();
			}
			// A stale nonce or a failed bot check is retried once, silently.
			if (!retried && !answer.content && (e.code === 'dsc_bad_nonce' || (e.code === 'dsc_turnstile' && e.status === 403) || e.code === 'dsc_turnstile_client')) {
				bubble.remove();
				self.busy = false;
				(e.code === 'dsc_bad_nonce' ? self.ensureSession(true) : Promise.resolve()).then(function () { self.request(mode, true); });
				return;
			}
			if (answer.content) {
				answer.incomplete = true;
				self.finishAnswer(bubble, answer, false);
			} else {
				bubble.remove();
			}
			self.busy = false;
			self.controller = null;
			self.updateSendState();
			self.handleError(e, mode);
		});
	};

	/* ---------------- compare: one send, Fast and Pro in parallel ---------------- */

	// One stream into one column. Resolves with the answer; rejects with the error object.
	Chat.prototype.streamInto = function (mode, messages, bubble, answer, signal, retried) {
		var self = this;
		var reasonStart = 0;
		var t0 = 0;
		return this.takeToken().then(function (token) {
			if (signal.aborted) { throw { name: 'AbortError' }; }
			t0 = Date.now();
			return fetch(self.cfg.chat, {
				method: 'POST', credentials: 'same-origin', cache: 'no-store',
				headers: { 'Content-Type': 'application/json', 'X-WP-Nonce': self.session ? self.session.nonce : '' },
				body: JSON.stringify({ messages: messages, mode: mode, ts: token, hp: self.el.hp ? self.el.hp.value : '' }),
				signal: signal
			});
		}).then(function (res) {
			var type = res.headers.get('Content-Type') || '';
			if (!res.ok || type.indexOf('text/event-stream') === -1) {
				return res.json().catch(function () { return {}; }).then(function (j) {
					throw { code: j.code || 'dsc_http', message: j.message || 'Something went wrong. Please try again.', data: j.data || {}, status: res.status };
				});
			}
			return self.readStream(res, {
				reasoning: function () { reasonStart = Date.now(); self.setStatus(bubble, 'Reasoningâ¦', true); },
				delta: function (t) {
					if (reasonStart && !answer.reasoned_ms) {
						answer.reasoned_ms = Date.now() - reasonStart;
						self.setStatus(bubble, 'Reasoned for ' + Math.max(1, Math.round(answer.reasoned_ms / 1000)) + ' s', false);
					}
					answer.content += t;
					if (!bubble.__queued) {
						bubble.__queued = true;
						requestAnimationFrame(function () { bubble.__queued = false; self.renderAnswer(bubble, answer, false); self.scrollToBottom(); });
					}
				},
				done: function (d) {
					if (d.reasoned_ms && reasonStart) { answer.reasoned_ms = d.reasoned_ms; }
					answer.total_ms = Date.now() - t0;
					if (d.finish === 'length' || d.finish === 'interrupted') { answer.incomplete = true; }
					if (d.quota) { self.applyQuota(d.quota); }
					if (d.paused) { self.paused = true; self.applyAvailability(); }
				},
				error: function (d) {
					if (d.quota) { self.applyQuota(d.quota); }
					throw { code: d.code || 'dsc_upstream', message: d.message, data: d, streamed: true };
				}
			});
		}).then(function () { return answer; }, function (err) {
			var e = err || {};
			if (e.data && e.data.quota) { self.applyQuota(e.data.quota); }
			if (!retried && !answer.content && ((e.code === 'dsc_turnstile' && e.status === 403) || e.code === 'dsc_turnstile_client')) {
				return self.streamInto(mode, messages, bubble, answer, signal, true);
			}
			throw e;
		});
	};

	Chat.prototype.compareNode = function (pair, streaming) {
		var self = this;
		var wrap = make('div', 'cdc-compare');
		var cols = {};
		['fast', 'pro'].forEach(function (mode) {
			var col = make('div', 'cdc-compare__col');
			col.appendChild(make('p', 'cdc-compare__label', mode === 'fast' ? 'Fast' : 'Pro'));
			var node = make('div', 'cdc-msg cdc-msg--assistant');
			node.appendChild(make('p', 'cdc-msg__status'));
			node.appendChild(make('div', 'cdc-msg__body cdc-md'));
			node.appendChild(make('div', 'cdc-msg__actions'));
			if (streaming) {
				node.setAttribute('aria-busy', 'true');
				node.classList.add('is-streaming');
				qs('.cdc-msg__body', node).innerHTML = '<span class="cdc-typing" aria-label="Writing"><i></i><i></i><i></i></span>';
			} else {
				self.renderAnswer(node, pair[mode], true);
				self.compareStatus(node, pair[mode], mode);
				node.setAttribute('aria-busy', 'false');
			}
			col.appendChild(node);
			wrap.appendChild(col);
			cols[mode] = node;
		});
		this.el.log.appendChild(wrap);
		wrap.__cols = cols;
		return wrap;
	};

	Chat.prototype.compareStatus = function (node, a, mode) {
		var s = qs('.cdc-msg__status', node);
		var parts = [];
		if (a && a.error) { parts.push(a.error); }
		if (mode === 'pro' && a && a.reasoned_ms) { parts.push('Reasoned for ' + Math.max(1, Math.round(a.reasoned_ms / 1000)) + ' s'); }
		if (mode === 'fast' && a && a.content) { parts.push('No reasoning step'); }
		if (a && a.total_ms) { parts.push('answered in ' + (a.total_ms / 1000).toFixed(1) + ' s'); }
		s.textContent = parts.join(' Â· ');
		s.classList.remove('is-active');
	};

	Chat.prototype.requestCompare = function () {
		var self = this;
		var messages = this.payloadMessages();
		var pair = { fast: { role: 'assistant', content: '', mode: 'fast' }, pro: { role: 'assistant', content: '', mode: 'pro' } };
		this.el.empty.hidden = true;
		var wrap = this.compareNode(pair, true);
		this.busy = true;
		this.updateSendState();
		this.controller = new AbortController();
		var signal = this.controller.signal;
		var firstError = null;

		function run(mode) {
			return self.streamInto(mode, messages, wrap.__cols[mode], pair[mode], signal).catch(function (e) {
				if (e && e.name === 'AbortError') { pair[mode].incomplete = true; return; }
				if (!firstError) { firstError = e; }
				pair[mode].error = (e && e.code && e.message) || 'This answer could not be generated.';
				if (pair[mode].content) { pair[mode].incomplete = true; }
			}).then(function () {
				var node = wrap.__cols[mode];
				self.renderAnswer(node, pair[mode], true);
				self.compareStatus(node, pair[mode], mode);
				node.setAttribute('aria-busy', 'false');
			});
		}

		this.ensureSession().then(function (s) {
			if (!s) { throw { code: 'dsc_session', message: 'The chat could not connect. Check your connection and try again.' }; }
			return Promise.all([run('fast'), run('pro')]);
		}).catch(function (e) { firstError = firstError || e; }).then(function () {
			self.busy = false;
			self.controller = null;
			if (!pair.fast.content && !pair.pro.content) {
				wrap.remove();
				self.updateSendState();
				if (signal.aborted) { self.ensureSession(true); return; }
				self.handleError(firstError || {}, 'fast');
				return;
			}
			self.chat.messages.push({ role: 'assistant', content: pair.pro.content || pair.fast.content, ts: Date.now(), mode: 'compare', compare: pair });
			self.persist();
			self.updateSendState();
			self.updateContext();
			self.scrollToBottom();
			if (signal.aborted) { self.ensureSession(true); }
		});
	};

	Chat.prototype.readStream = function (res, on) {
		var reader = res.body.getReader();
		var decoder = new TextDecoder();
		var buffer = '';
		function dispatch(block) {
			var event = 'message', data = '';
			block.split('\n').forEach(function (line) {
				if (line.indexOf('event:') === 0) { event = line.slice(6).trim(); }
				else if (line.indexOf('data:') === 0) { data += line.slice(5).trim(); }
			});
			if (!data) { return; }
			var payload;
			try { payload = JSON.parse(data); } catch (e) { return; }
			if (event === 'delta' && typeof payload.t === 'string') { on.delta(payload.t); }
			else if (event === 'reasoning') { on.reasoning(); }
			else if (event === 'done') { on.done(payload); }
			else if (event === 'error') { on.error(payload); }
		}
		function pump() {
			return reader.read().then(function (r) {
				if (r.done) { if (buffer.trim()) { dispatch(buffer); } return; }
				buffer += decoder.decode(r.value, { stream: true }).replace(/\r/g, '');
				var idx;
				while ((idx = buffer.indexOf('\n\n')) !== -1) {
					var block = buffer.slice(0, idx);
					buffer = buffer.slice(idx + 2);
					dispatch(block);
				}
				return pump();
			});
		}
		return pump();
	};

	Chat.prototype.stop = function () {
		if (this.controller) { this.controller.abort(); }
	};

	Chat.prototype.finishAnswer = function (bubble, answer, stopped) {
		this.busy = false;
		this.controller = null;
		if (!answer.content && stopped) {
			answer.content = '';
		}
		if (answer.content || stopped) {
			this.chat.messages.push(answer);
		}
		this.renderAnswer(bubble, answer, true);
		bubble.setAttribute('aria-busy', 'false');
		this.markLastActions();
		this.persist();
		this.updateSendState();
		this.updateContext();
		this.scrollToBottom();
	};

	Chat.prototype.handleError = function (e, mode) {
		var code = e.code || '';
		var msg = (code && e.message) || 'Something went wrong. Please try again.';
		if (code === 'dsc_paused') { this.paused = true; this.applyAvailability(); return; }
		if (code === 'dsc_day_limit' || code === 'dsc_pro_limit') {
			var q = this.quota;
			if (code === 'dsc_pro_limit' && mode === 'pro') { this.setMode('fast'); }
			this.showNotice(msg + (q ? ' Resets at ' + resetTime(q.resets_at) + '.' : ''), 'warn', true);
			return;
		}
		if (code === 'dsc_rate_limit') { this.showNotice(msg, 'warn'); this.appendError(msg, false); return; }
		if (code === 'dsc_consent') { this.appendError(msg, true); if (this.noticeEl) { qs('.cdc-consent__ok', this.noticeEl).focus(); } return; }
		this.appendError(msg, code !== 'dsc_too_long' && code !== 'dsc_bad_image' && code !== 'dsc_too_many_images');
	};

	/* ---------------- rendering ---------------- */

	Chat.prototype.renderAll = function () {
		var self = this;
		qsa('.cdc-compare, .cdc-msg, .cdc-error', this.el.log).forEach(function (n) { n.remove(); });
		this.el.empty.hidden = this.chat.messages.length > 0;
		this.chat.messages.forEach(function (m, i) { self.appendMessage(m, i); });
		this.markLastActions();
		this.updateContext();
		this.updateFullLink();
		this.stickToBottom = true;
		this.scrollToBottom();
	};

	Chat.prototype.appendMessage = function (m, index, streaming) {
		this.el.empty.hidden = true;
		if (m.role === 'assistant' && m.compare && !streaming) { return this.compareNode(m.compare, false); }
		var node = make('div', 'cdc-msg cdc-msg--' + m.role);
		if (m.role === 'user') {
			if (m.images && m.images.length) {
				var gallery = make('div', 'cdc-msg__images');
				m.images.forEach(function (im) {
					var img = make('img');
					img.src = im.thumb || im.data;
					img.alt = 'Attached image';
					img.width = 96; img.height = 96;
					img.loading = 'lazy';
					gallery.appendChild(img);
				});
				node.appendChild(gallery);
			}
			if (m.content) { node.appendChild(make('div', 'cdc-msg__text', m.content)); }
		} else {
			node.appendChild(make('p', 'cdc-msg__status'));
			node.appendChild(make('div', 'cdc-msg__body cdc-md'));
			node.appendChild(make('div', 'cdc-msg__actions'));
			if (streaming) {
				node.setAttribute('aria-busy', 'true');
				node.classList.add('is-streaming');
				qs('.cdc-msg__body', node).innerHTML = '<span class="cdc-typing" aria-label="Writing"><i></i><i></i><i></i></span>';
			} else {
				this.renderAnswer(node, m, true);
			}
		}
		this.el.log.appendChild(node);
		if (!streaming) { node.setAttribute('aria-busy', 'false'); }
		return node;
	};

	Chat.prototype.setStatus = function (bubble, text, active) {
		var s = qs('.cdc-msg__status', bubble);
		s.textContent = text;
		s.classList.toggle('is-active', !!active);
		if (active) { qs('.cdc-msg__body', bubble).innerHTML = ''; }
	};

	Chat.prototype.queueRender = function (bubble, answer) {
		var self = this;
		if (this.renderQueued) { return; }
		this.renderQueued = true;
		requestAnimationFrame(function () {
			self.renderQueued = false;
			self.renderAnswer(bubble, answer, false);
			self.scrollToBottom();
		});
	};

	Chat.prototype.renderAnswer = function (node, m, final) {
		var body = qs('.cdc-msg__body', node);
		var status = qs('.cdc-msg__status', node);
		if (final) { node.classList.remove('is-streaming'); }
		if (m.reasoned_ms && status && !status.classList.contains('is-active')) {
			status.textContent = 'Reasoned for ' + Math.max(1, Math.round(m.reasoned_ms / 1000)) + ' s';
		}
		if (!m.reasoned_ms && final && status && status.classList.contains('is-active')) { status.textContent = ''; status.classList.remove('is-active'); }
		body.innerHTML = m.content ? renderMarkdown(m.content) : (final ? '' : body.innerHTML);
		node.setAttribute('data-raw', m.content || '');
		this.enhance(body);
		if (final) {
			var actions = qs('.cdc-msg__actions', node);
			actions.innerHTML = '';
			if (m.incomplete) { actions.appendChild(make('span', 'cdc-tag', m.content ? 'Stopped Â· incomplete' : 'Stopped before an answer')); }
			if (m.helpdesk && m.content) { actions.appendChild(make('span', 'cdc-tag cdc-tag--help', 'DeepSeek helpdesk Â· answers from dated, verified facts')); }
			if (m.content) {
				var copy = make('button', 'cdc-act', 'Copy');
				copy.type = 'button'; copy.setAttribute('data-act', 'copy-answer');
				actions.appendChild(copy);
			}
		}
	};

	Chat.prototype.enhance = function (body) {
		qsa('pre', body).forEach(function (pre) {
			if (pre.parentNode.classList.contains('cdc-code')) { return; }
			var code = qs('code', pre);
			var lang = code && code.className.match(/language-([\w+#-]+)/);
			var wrap = make('div', 'cdc-code');
			var head = make('div', 'cdc-code__head');
			head.appendChild(make('span', 'cdc-code__lang', lang ? lang[1] : 'code'));
			var btn = make('button', 'cdc-act', 'Copy code');
			btn.type = 'button'; btn.setAttribute('data-act', 'copy-code');
			head.appendChild(btn);
			pre.parentNode.insertBefore(wrap, pre);
			wrap.appendChild(head);
			wrap.appendChild(pre);
		});
		qsa('table', body).forEach(function (t) {
			if (t.parentNode.classList.contains('cdc-table')) { return; }
			var wrap = make('div', 'cdc-table');
			wrap.tabIndex = 0;
			t.parentNode.insertBefore(wrap, t);
			wrap.appendChild(t);
		});
	};

	Chat.prototype.markLastActions = function () {
		qsa('.cdc-act--regen', this.el.log).forEach(function (b) { b.remove(); });
		var answers = qsa('.cdc-msg--assistant', this.el.log);
		var last = answers[answers.length - 1];
		var lastMsg = this.chat.messages[this.chat.messages.length - 1];
		if (!last || !lastMsg || lastMsg.role !== 'assistant') { return; }
		var regen = make('button', 'cdc-act cdc-act--regen', 'Regenerate');
		regen.type = 'button'; regen.setAttribute('data-act', 'regenerate');
		qs('.cdc-msg__actions', last).appendChild(regen);
	};

	Chat.prototype.appendError = function (text, retry) {
		var node = make('div', 'cdc-error');
		node.setAttribute('role', 'alert');
		node.appendChild(make('span', '', text));
		if (retry) {
			var b = make('button', 'cdc-act', 'Retry');
			b.type = 'button'; b.setAttribute('data-act', 'retry');
			node.appendChild(b);
		}
		this.el.log.appendChild(node);
		this.stickToBottom = true;
		this.scrollToBottom();
	};

	Chat.prototype.scrollToBottom = function () {
		if (!this.stickToBottom) { return; }
		var l = this.el.log;
		l.scrollTo({ top: l.scrollHeight, behavior: reduceMotion ? 'auto' : 'auto' });
	};

	Chat.prototype.updateContext = function () {
		var n = this.chat.messages.filter(function (m) { return !m.error; }).length;
		var pct = Math.round(Math.min(n, this.cfg.context) / this.cfg.context * 100);
		this.el.ctxValue.textContent = pct + '%';
		this.el.ctxFill.style.width = pct + '%';
		qs('.cdc-context', this.root).title = 'Last ' + this.cfg.context + ' messages are sent as context. Start a new chat to clear it.';
	};

	Chat.prototype.updateFullLink = function () {
		if (!this.el.fullLinks || !this.el.fullLinks.length) { return; }
		var saved = this.chat.messages.length && !this.prefs.privacy;
		var href = this.cfg.full + (saved ? '?c=' + encodeURIComponent(this.chat.id) : '');
		this.el.fullLinks.forEach(function (a) { a.href = href; });
	};

	/* ---------------- conversations ---------------- */

	Chat.prototype.persist = function () {
		this.chat.updated = Date.now();
		this.updateFullLink();
		if (this.prefs.privacy || !this.chat.messages.length) { return; }
		var self = this;
		Store.put(JSON.parse(JSON.stringify(this.chat))).then(function () { if (self.full) { self.renderList(); } });
	};

	Chat.prototype.newChat = function () {
		if (this.busy) { this.stop(); }
		this.chat = this.blankChat();
		this.renderAll();
		this.hideNoticeIfTransient();
		if (this.full) {
			this.renderList();
			try { history.replaceState(null, '', window.location.pathname); } catch (e) { /* ignore */ }
		}
		this.el.input.focus();
	};
	Chat.prototype.hideNoticeIfTransient = function () {
		if (this.el.notice.getAttribute('data-sticky') !== '1') { this.hideNotice(); }
	};

	Chat.prototype.openChat = function (id) {
		var self = this;
		if (this.busy) { this.stop(); }
		Store.get(id).then(function (chat) {
			if (!chat) { return; }
			self.chat = chat;
			self.renderAll();
			self.renderList();
		});
	};

	Chat.prototype.renderList = function () {
		if (!this.el.list) { return; }
		var self = this;
		Store.all().then(function (chats) {
			var list = self.el.list;
			list.innerHTML = '';
			if (!chats.length) { list.appendChild(make('p', 'cdc-list__empty', 'No saved conversations yet.')); return; }
			var groups = {};
			var order = ['Today', 'Yesterday', 'Earlier'];
			chats.forEach(function (c) { var g = dayLabel(c.updated); (groups[g] = groups[g] || []).push(c); });
			order.forEach(function (g) {
				if (!groups[g]) { return; }
				list.appendChild(make('p', 'cdc-list__group', g));
				var ul = make('ul', 'cdc-list__items');
				groups[g].forEach(function (c) {
					var li = make('li', 'cdc-item' + (c.id === self.chat.id ? ' is-current' : ''));
					var open = make('button', 'cdc-item__open', c.title || 'Untitled chat');
					open.type = 'button'; open.setAttribute('data-act', 'open-chat'); open.setAttribute('data-id', c.id);
					if (c.id === self.chat.id) { open.setAttribute('aria-current', 'true'); }
					var ren = make('button', 'cdc-item__tool', 'Rename');
					ren.type = 'button'; ren.setAttribute('data-act', 'rename-chat'); ren.setAttribute('data-id', c.id);
					ren.setAttribute('aria-label', 'Rename â' + (c.title || 'Untitled chat') + 'â');
					var del = make('button', 'cdc-item__tool', 'Delete');
					del.type = 'button'; del.setAttribute('data-act', 'delete-chat'); del.setAttribute('data-id', c.id);
					del.setAttribute('aria-label', 'Delete â' + (c.title || 'Untitled chat') + 'â');
					li.appendChild(open); li.appendChild(ren); li.appendChild(del);
					ul.appendChild(li);
				});
				list.appendChild(ul);
			});
		});
	};

	Chat.prototype.startRename = function (id) {
		var self = this;
		var btn = qs('.cdc-item__open[data-id="' + id + '"]', this.root);
		if (!btn) { return; }
		var input = make('input', 'cdc-item__rename');
		input.type = 'text';
		input.value = btn.textContent;
		input.maxLength = 80;
		input.setAttribute('aria-label', 'New name');
		btn.replaceWith(input);
		input.focus(); input.select();
		var done = false;
		function commit(save) {
			if (done) { return; }
			done = true;
			Store.get(id).then(function (chat) {
				if (chat && save && input.value.trim()) {
					chat.title = input.value.trim().slice(0, 80);
					if (id === self.chat.id) { self.chat.title = chat.title; }
					return Store.put(chat);
				}
			}).then(function () { self.renderList(); });
		}
		input.addEventListener('keydown', function (e) {
			if (e.key === 'Enter') { e.preventDefault(); commit(true); }
			if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); commit(false); }
		});
		input.addEventListener('blur', function () { commit(true); });
	};

	Chat.prototype.exportText = function (format) {
		var c = this.chat;
		var name = this.cfg.name;
		var lines = [];
		if (format === 'md') { lines.push('# ' + (c.title || 'Chat'), ''); } else { lines.push(c.title || 'Chat', ''); }
		c.messages.forEach(function (m) {
			var who = m.role === 'user' ? 'You' : name;
			var text = m.content || '';
			if (m.compare) { text = 'Fast:\n' + (m.compare.fast.content || '(no answer)') + '\n\nPro:\n' + (m.compare.pro.content || '(no answer)'); }
			if (m.images && m.images.length) { text += (text ? '\n' : '') + '[' + m.images.length + ' image(s)]'; }
			if (m.incomplete) { text += '\n\n(incomplete)'; }
			if (format === 'md') { lines.push('**' + who + ':**', '', text, ''); }
			else { lines.push(who + ':', text.replace(/```[\w-]*\n?/g, ''), ''); }
		});
		return lines.join('\n').trim() + '\n';
	};

	Chat.prototype.prepareDownload = function (link, format) {
		var blob = new Blob([this.exportText(format)], { type: format === 'md' ? 'text/markdown' : 'text/plain' });
		if (link.href && link.href.indexOf('blob:') === 0) { URL.revokeObjectURL(link.href); }
		link.href = URL.createObjectURL(blob);
		var base = (this.chat.title || 'chat').replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '-').slice(0, 40) || 'chat';
		link.setAttribute('download', base + '.' + format);
	};

	/* ------------------------------------------------------------------ */

	function boot() {
		qsa('.cdc[data-cdc]').forEach(function (root) {
			if (!root.__cdc) { root.__cdc = new Chat(root); }
		});
	}
	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', boot);
	} else {
		boot();
	}
})();
