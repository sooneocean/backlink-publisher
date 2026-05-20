// Plan 2026-05-20-002 Unit 4 — homepage URL auto-derive frontend.
//
// Wires the new paste_url top input on `index.html`. On paste/blur/Enter:
//   1. derivePathTiers(rawUrl) — pure path-depth derivation (mirrors
//      backlink_publisher._util.url_derive.derive_path_tiers 1:1 — same
//      _CATEGORY_TOKEN regex, same branch structure)
//   2. Writes derived values to existing main_url|category_url|work_url
//      inputs (overwrite always — v1.0 has no lock state; v1.1 R4 adds it)
//   3. Sequential await verifyTier(...) for main → category → work (NOT
//      Promise.all — three same-host calls would burn per-host=1 throttle
//      and surface host_busy UX false-positives. Wall-clock ~3-6s p50,
//      ~15s p99)
//   4. R3.5 title pairwise compare: category title == main title → mark
//      category as ⚠ "looks like homepage fallback"
//   5. Writes status text to <span class="verify-status">.
//
// Companion: webui_app/routes/url_verify.py (POST /url-verify)
// Plan ref: docs/plans/2026-05-20-002-feat-homepage-url-autoderive-v1-plan.md
(function () {
  'use strict';

  const META_CSRF = document.querySelector('meta[name="csrf-token"]');
  const CSRF_TOKEN = META_CSRF ? META_CSRF.content : '';

  const VERIFY_TIMEOUT_MS = 6000;  // server hard cap 5s + 1s slack
  const PASTE_DEDUP_MS = 500;       // dedup rapid-fire paste events

  // _CATEGORY_TOKEN — mirror of Python regex. Letters only, 3-15 chars,
  // no digits, no hyphens. Hyphenated slugs (post-slug, my-article) are
  // work URLs in the wild, not category landing pages.
  const _CATEGORY_TOKEN = /^[a-z]{3,15}$/i;

  // ── Pure deriver (mirror of Python derive_path_tiers) ────────────────

  function derivePathTiers(rawUrl) {
    const none = { main: null, category: null, work: null };
    if (typeof rawUrl !== 'string' || !rawUrl) return none;
    let parsed;
    try {
      parsed = new URL(rawUrl);
    } catch (_) {
      return none;
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return none;
    }
    if (!parsed.host) return none;

    // R2 normalization: scheme=https, drop query+fragment, trim trailing
    // slash on subpaths (keep root /). Host preserved verbatim.
    const origin = 'https://' + parsed.host;
    const segments = parsed.pathname.split('/').filter(function (s) { return s !== ''; });

    if (segments.length === 0) {
      return { main: origin, category: null, work: null };
    }

    const subpath = function (segs) {
      const p = '/' + segs.join('/');
      return 'https://' + parsed.host + (p === '/' ? '' : p.replace(/\/+$/, ''));
    };

    if (segments.length === 1) {
      return { main: origin, category: subpath(segments), work: null };
    }

    const tail = segments[segments.length - 1];
    if (_CATEGORY_TOKEN.test(tail)) {
      return { main: origin, category: subpath(segments), work: null };
    }
    return {
      main: origin,
      category: subpath(segments.slice(0, -1)),
      work: subpath(segments),
    };
  }

  // ── Status copy mapping (per plan Status Copy Reference Table) ───────

  const REASON_COPY = {
    'ok': { glyph: '✓', text: '', aria: '已验证' },
    'timeout': { glyph: '⚠', text: '超时', aria: '验证超时，5秒内未响应' },
    'http_4xx': { glyph: '⚠', text: 'HTTP 4xx', aria: 'HTTP 客户端错误' },
    'http_5xx': { glyph: '⚠', text: '服务器错误', aria: '服务器错误' },
    'http_200_no_title': { glyph: '⚠', text: '无标题', aria: '页面无标题' },
    'body_too_small': { glyph: '⚠', text: '内容过短', aria: '页面内容过短，可能是 SPA 加载占位' },
    'soft_404_title': { glyph: '⚠', text: '疑似 404', aria: '页面标题疑似 404' },
    'network_error': { glyph: '⚠', text: '网络错误', aria: '网络错误' },
    'ssrf_blocked': { glyph: '⛔', text: '拒绝（私有地址）', aria: '拒绝私有地址' },
    'blocked_scheme': { glyph: '⛔', text: '协议不支持', aria: '仅支持 http/https' },
    'invalid_url': { glyph: '⛔', text: 'URL 格式无效', aria: 'URL 格式无效' },
    'rate_limited': { glyph: '⏳', text: '排队中', aria: '请求频率受限，稍候重试' },
    'host_busy': { glyph: '⏳', text: '排队中', aria: '同主机连接占用，稍候' },
    'upstream_overloaded': { glyph: '⏳', text: '验证排队中', aria: '服务繁忙，稍候' },
    'homepage_match': { glyph: '⚠', text: '与首页相同', aria: '分类页与首页 title 相同，疑似 SPA fallback' },
  };

  function reasonHttpKey(reason) {
    // Map http_404 / http_500 → http_4xx / http_5xx for copy lookup
    if (typeof reason === 'string' && reason.startsWith('http_')) {
      const code = parseInt(reason.slice(5), 10);
      if (code >= 400 && code < 500) return 'http_4xx';
      if (code >= 500 && code < 600) return 'http_5xx';
    }
    return reason;
  }

  function setStatus(spanEl, state, opts) {
    if (!spanEl) return;
    opts = opts || {};
    spanEl.className = 'verify-status';
    if (state === 'pending') {
      spanEl.textContent = '⏳ 验证中...';
      spanEl.setAttribute('aria-label', '验证中');
      return;
    }
    if (state === 'ok') {
      const title = (opts.title || '').slice(0, 24);
      spanEl.classList.add('text-success');
      spanEl.textContent = title ? '✓ ' + title : '✓ 已验证（无标题）';
      spanEl.setAttribute('aria-label', title ? '已验证：' + opts.title : '已验证，无标题');
      if (opts.title) spanEl.title = opts.title;
      return;
    }
    if (state === 'warn' || state === 'block') {
      const key = reasonHttpKey(opts.reason);
      const copy = REASON_COPY[key] || REASON_COPY['network_error'];
      spanEl.classList.add(state === 'warn' ? 'text-warning' : 'text-danger');
      spanEl.textContent = copy.glyph + ' ' + copy.text;
      spanEl.setAttribute('aria-label', copy.aria);
      return;
    }
    if (state === 'disabled') {
      spanEl.classList.add('text-muted');
      spanEl.textContent = '◌ 验证已关闭';
      spanEl.setAttribute('aria-label', 'BACKLINK_NO_FETCH_VERIFY 已设置，跳过验证');
      return;
    }
    if (state === 'idle') {
      spanEl.textContent = '';
      spanEl.removeAttribute('aria-label');
      spanEl.removeAttribute('title');
      return;
    }
    if (state === 'none') {
      spanEl.textContent = '—';
      spanEl.setAttribute('aria-label', '该层级留空');
      return;
    }
  }

  // ── Verifier (single POST) ───────────────────────────────────────────

  async function verifyTier(url, abortSignal) {
    const ctrl = new AbortController();
    const timer = setTimeout(function () { ctrl.abort(); }, VERIFY_TIMEOUT_MS);
    if (abortSignal) {
      abortSignal.addEventListener('abort', function () { ctrl.abort(); });
    }
    try {
      const resp = await fetch('/url-verify', {
        method: 'POST',
        signal: ctrl.signal,
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF_TOKEN,
        },
        body: JSON.stringify({ url: url }),
      });
      if (resp.status === 204) {
        return { _disabled: true };
      }
      if (resp.status === 403) {
        return { ok: false, reason: 'network_error', _http: 403 };
      }
      return await resp.json();
    } catch (e) {
      const reason = (e && e.name === 'AbortError') ? 'timeout' : 'network_error';
      return { ok: false, reason: reason, title: '' };
    } finally {
      clearTimeout(timer);
    }
  }

  // ── Orchestrator (sequential, NOT Promise.all) ──────────────────────

  async function verifyAll(tiers, statusEls, abortSignal) {
    // Set all to pending simultaneously for visual feedback.
    ['main', 'category', 'work'].forEach(function (key) {
      if (tiers[key]) {
        setStatus(statusEls[key], 'pending');
      } else {
        setStatus(statusEls[key], 'none');
      }
    });

    const results = { main: null, category: null, work: null };

    // Serial main → category → work. Each call respects 5s server cap.
    // Total ~3-6s p50, ~15s p99. Trade-off vs. Promise.all: same-host
    // requests would burn per-host=1 throttle, surfacing host_busy on
    // 2/3 chips. Serializing gives correct UX at cost of wall-clock.
    for (const key of ['main', 'category', 'work']) {
      if (!tiers[key]) continue;
      if (abortSignal && abortSignal.aborted) return results;
      const result = await verifyTier(tiers[key], abortSignal);
      results[key] = result;

      if (result._disabled) {
        // Server is in BACKLINK_NO_FETCH_VERIFY mode — paint all three
        // chips as disabled and bail out.
        ['main', 'category', 'work'].forEach(function (k) {
          if (tiers[k]) setStatus(statusEls[k], 'disabled');
        });
        return results;
      }

      // R3.5 title pairwise compare on category: if it matches main's
      // title, mark as homepage_match (SPA fallback heuristic).
      let displayResult = result;
      if (key === 'category' && results.main && result.ok
          && result.title && results.main.title
          && result.title === results.main.title) {
        displayResult = { ok: false, reason: 'homepage_match', title: result.title };
      }

      if (displayResult.ok) {
        setStatus(statusEls[key], 'ok', { title: displayResult.title || '' });
      } else {
        const sevState = (displayResult.reason === 'ssrf_blocked'
                         || displayResult.reason === 'blocked_scheme'
                         || displayResult.reason === 'invalid_url') ? 'block' : 'warn';
        setStatus(statusEls[key], sevState, { reason: displayResult.reason });
      }
    }
    return results;
  }

  // ── DOM binding ──────────────────────────────────────────────────────

  function clearStatusOnEdit(inputEl, spanEl) {
    if (!inputEl || !spanEl) return;
    inputEl.addEventListener('input', function () {
      // Manual edit clears stale status — stale ✓ does not lie.
      setStatus(spanEl, 'idle');
    });
  }

  function bindPasteInput(pasteEl, targets, statusEls) {
    if (!pasteEl) return;

    let lastTriggerAt = 0;
    let inflightCtrl = null;

    async function trigger(rawValue) {
      const now = Date.now();
      if (now - lastTriggerAt < PASTE_DEDUP_MS) return;
      lastTriggerAt = now;

      const tiers = derivePathTiers(rawValue);
      if (!tiers.main) {
        // Invalid URL — leave inputs alone, set all statuses idle.
        setStatus(statusEls.main, 'idle');
        setStatus(statusEls.category, 'idle');
        setStatus(statusEls.work, 'idle');
        return;
      }

      // Write derived values (v1.0: overwrite always — no lock state).
      if (targets.main) targets.main.value = tiers.main;
      if (targets.category) targets.category.value = tiers.category || '';
      if (targets.work) targets.work.value = tiers.work || '';

      // Cancel any in-flight verify and start a new one.
      if (inflightCtrl) inflightCtrl.abort();
      inflightCtrl = new AbortController();
      try {
        await verifyAll(tiers, statusEls, inflightCtrl.signal);
      } catch (_) { /* aborted */ }
    }

    pasteEl.addEventListener('paste', function (e) {
      // setTimeout to read post-paste value.
      setTimeout(function () { trigger(pasteEl.value); }, 0);
    });
    pasteEl.addEventListener('blur', function () {
      trigger(pasteEl.value);
    });
    pasteEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        trigger(pasteEl.value);
      }
    });

    // Clear status on manual edit of any downstream input (no lock state
    // in v1.0; stale ✓ would mislead operator).
    clearStatusOnEdit(targets.main, statusEls.main);
    clearStatusOnEdit(targets.category, statusEls.category);
    clearStatusOnEdit(targets.work, statusEls.work);
  }

  // ── Public API (window-attached for inline init in index.html) ──────

  window.urlDerive = {
    derivePathTiers: derivePathTiers,
    bindPasteInput: bindPasteInput,
    verifyAll: verifyAll,
    verifyTier: verifyTier,
    _CATEGORY_TOKEN: _CATEGORY_TOKEN,
  };
})();
