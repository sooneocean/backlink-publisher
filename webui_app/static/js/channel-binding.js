// Plan 2026-05-19-006 Unit 5 — channel binding dashboard JS.
//
// Wires the Verify Token + Bind buttons rendered by the
// dashboard_channel_card macro. Uses AbortController for timeout,
// X-CSRFToken header (Unit 4 extension), and a 1s per-(channel,action)
// debounce to prevent double-submit (solution lesson:
// webui-blocking-subprocess-and-missing-progress-feedback).
//
// Companion: webui_app/templates/_channel_card_macro.html
// Endpoints: /api/<channel>/verify (Unit 4); bind delegates to existing channel UI
(function () {
  'use strict';

  const META_CSRF = document.querySelector('meta[name="csrf-token"]');
  const CSRF_TOKEN = META_CSRF ? META_CSRF.content : '';

  const VERIFY_TIMEOUT_MS = 6000;  // server hard cap is 5s + 1s slack
  const DEBOUNCE_MS = 1000;

  // Tracks last-click-time per `${channel}:${action}` for debounce.
  const lastClickAt = new Map();

  async function callJson(url, body, timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(function () { ctrl.abort(); }, timeoutMs);
    try {
      const resp = await fetch(url, {
        method: 'POST',
        signal: ctrl.signal,
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF_TOKEN,
        },
        body: body ? JSON.stringify(body) : null,
      });
      if (resp.status === 404) {
        return { _http_status: 404, _error: 'channel not registered' };
      }
      if (resp.status === 403) {
        return { _http_status: 403, _error: 'CSRF / auth rejected' };
      }
      const ct = resp.headers.get('content-type') || '';
      if (!ct.includes('application/json')) {
        return { _http_status: resp.status, _error: 'unexpected content-type: ' + ct.split(';')[0] };
      }
      return await resp.json();
    } catch (e) {
      return { _error: e && e.name === 'AbortError' ? 'timeout' : String(e) };
    } finally {
      clearTimeout(timer);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderResult(card, result) {
    const resultEl = card.querySelector('[data-field="result"]');
    if (!resultEl) return;
    resultEl.style.display = 'block';

    let cls, label, detail;
    if (result._error === 'timeout') {
      cls = 'warn';
      label = 'timeout';
      detail = '伺服器逾時';
    } else if (result._http_status === 404) {
      cls = 'err';
      label = '404';
      detail = '渠道未註冊';
    } else if (result._http_status === 403) {
      cls = 'err';
      label = '403';
      detail = 'CSRF 或認證被拒';
    } else if (result._error) {
      cls = 'err';
      label = 'error';
      detail = result._error;
    } else if (result.ok) {
      cls = 'ok';
      label = result.last_verify_result || 'ok';
      detail = result.identity ? '身份: ' + result.identity : '';
    } else {
      cls = 'err';
      label = result.last_verify_result || 'fail';
      detail = (result.blockers || []).join('; ');
    }

    resultEl.innerHTML =
      '<span class="badge-status ' + cls + '">' + escapeHtml(label) + '</span> ' +
      '<span class="dch-result-detail">' + escapeHtml(detail) + '</span>';

    // Also update the bound badge if verify came back with a clear ok/fail.
    if (result.ok === true || result.ok === false) {
      const boundBadge = card.querySelector('[data-field="bound"]');
      if (boundBadge) {
        boundBadge.classList.remove('ok', 'err');
        boundBadge.classList.add(result.ok ? 'ok' : 'err');
        boundBadge.textContent = result.ok ? '已驗證' : '未驗證';
      }
    }
  }

  function setBusy(btn, busy) {
    if (busy) {
      btn.dataset.originalHtml = btn.dataset.originalHtml || btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML =
        '<span class="spinner-border spinner-border-sm" role="status"></span> 處理中…';
    } else {
      btn.disabled = false;
      if (btn.dataset.originalHtml) {
        btn.innerHTML = btn.dataset.originalHtml;
      }
    }
  }

  function _triggerChannelBind(channel, btn, card) {
    if (channel === 'velog') {
      // Velog login lives in settings.js (Plan 007 U3). Bridge to it via a DOM
      // CustomEvent instead of probing a window global — under ES-module scope a
      // `typeof runVelogLogin` probe is always false and would silently no-op.
      document.dispatchEvent(new CustomEvent('velog:login'));
      return;
    }
    // medium / blogger — open the channel collapse section, then click bind btn.
    const panel = document.getElementById('channel-' + channel);
    if (!panel) {
      renderResult(card, { _error: 'channel panel not found: ' + channel });
      return;
    }
    function _clickBind() {
      var bindBtn = panel.querySelector('.bind-channel-btn');
      if (!bindBtn) {
        renderResult(card, { _error: '綁定按鈕不可用（可能因身份不符）' });
        return;
      }
      bindBtn.click();
    }
    if (panel.classList.contains('show')) {
      _clickBind();
    } else {
      panel.addEventListener('shown.bs.collapse', _clickBind, { once: true });
      bootstrap.Collapse.getOrCreateInstance(panel).show();
    }
  }

  document.addEventListener('click', async function (e) {
    const verifyBtn = e.target.closest('.dch-btn-verify');
    const dryRunBtn = e.target.closest('.dch-btn-dry-run');
    const bindBtn = e.target.closest('.dch-btn-bind');
    if (!verifyBtn && !dryRunBtn && !bindBtn) return;

    e.preventDefault();
    const btn = verifyBtn || dryRunBtn || bindBtn;
    const card = btn.closest('.dashboard-channel-card');
    const channel = btn.dataset.channel;
    const action = verifyBtn ? 'verify' : dryRunBtn ? 'dry-run' : 'bind';
    const debounceKey = channel + ':' + action;

    // Per-(channel, action) 1s debounce to absorb double-clicks.
    const now = Date.now();
    const prev = lastClickAt.get(debounceKey) || 0;
    if (now - prev < DEBOUNCE_MS) return;
    lastClickAt.set(debounceKey, now);

    if (action === 'bind') {
      _triggerChannelBind(channel, btn, card);
      return;
    }

    setBusy(btn, true);
    try {
      const url = '/api/' + encodeURIComponent(channel) + '/' + action;
      const result = await callJson(url, null, VERIFY_TIMEOUT_MS);
      renderResult(card, result);
    } finally {
      setBusy(btn, false);
    }
  });
})();
