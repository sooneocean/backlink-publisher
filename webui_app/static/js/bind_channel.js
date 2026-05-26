/*  bind_channel.js — Plan 2026-05-19-001 Unit 5.

    Wires every .bind-channel-btn on the settings page to:
      1. POST /settings/channels/<channel>/bind  (CSRF from <meta name="csrf-token">)
      2. Poll GET /settings/channels/<channel>/bind/<job_id> at 1Hz until
         status ∈ {done, failed}.
      3. Reflect status + last error message in the per-channel badge and
         show streaming events in the inline log area.
      4. Remember the clicked channel in sessionStorage so a page reload
         re-opens the same card (handled in settings.html DOMContentLoaded).

    No frameworks; vanilla DOM + fetch.
*/
(function () {
    'use strict';

    // Poll cadence: exponential backoff 1s → 2s → 4s, capped at 5s.
    // A flat 1s poll generated 60+ requests per minute for long binds
    // (Chrome attach can take 30-120s). The backoff drops that to ~10
    // requests per minute steady-state without sacrificing responsiveness
    // for fast binds (first poll still fires at 1s).
    var POLL_INITIAL_MS = 1000;
    var POLL_MAX_MS = 5000;
    var POLL_BACKOFF_FACTOR = 2;
    // After this many consecutive poll errors, surface failure to UI and
    // stop polling — protects operator from a silently-stuck loop when the
    // bind subprocess died or the server stopped responding.
    var POLL_MAX_CONSECUTIVE_ERRORS = 3;
    // Per-job mutable state — keyed by jobId so concurrent binds across
    // multiple channels don't interfere.
    var _pollState = {};
    var BADGE_TEXTS = {
        bound:   '已绑定 ✓',
        expired: '已过期 ⚠',
        unbound: '未绑定',
        running: '绑定中…',
        done:    '已绑定 ✓',
        failed:  '已过期 ⚠'
    };
    var BADGE_CLASSES = {
        bound:   'ok',
        expired: 'warn',
        unbound: 'err',
        running: 'warn',
        done:    'ok',
        failed:  'err'
    };

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') || '' : '';
    }

    function setBadge(channel, statusKey) {
        var badge = document.getElementById('bind-badge-' + channel);
        if (!badge) return;
        badge.textContent = BADGE_TEXTS[statusKey] || statusKey;
        badge.classList.remove('ok', 'warn', 'err');
        badge.classList.add(BADGE_CLASSES[statusKey] || 'err');
    }

    function appendLog(channel, line) {
        var log = document.getElementById('bind-log-' + channel);
        if (!log) return;
        log.style.display = 'block';
        var row = document.createElement('div');
        row.textContent = line;
        log.appendChild(row);
        log.scrollTop = log.scrollHeight;
    }

    function showRefreshNotice(channel) {
        var host = document.getElementById('bind-section-' + channel);
        if (!host) return;
        var existing = document.getElementById('bind-refresh-' + channel);
        if (existing) return;
        var notice = document.createElement('div');
        notice.id = 'bind-refresh-' + channel;
        notice.className = 'alert alert-success py-2 px-3 mt-3 mb-0';
        notice.setAttribute('role', 'status');
        notice.innerHTML =
            '<strong>授权已完成。</strong> 请刷新页面以更新当前状态。';
        host.appendChild(notice);
        try { notice.scrollIntoView({behavior: 'smooth', block: 'nearest'}); } catch (e) {}
    }

    function describeEvent(ev) {
        if (!ev || !ev.event) return '';
        switch (ev.event) {
            case 'channel.bind.start':          return '开始绑定…';
            case 'channel.bind.browser_ready':  return '浏览器已启动，请完成登录';
            case 'channel.bind.login_detected': return '登录成功，正在保存凭据…';
            case 'channel.bind.persisted':      return '凭据已保存 ✓';
            case 'channel.bind.failed':         return '绑定失败：' + (ev.error_code || 'unknown');
            default:                            return ev.event;
        }
    }

    function _initState(jobId) {
        if (!_pollState[jobId]) {
            _pollState[jobId] = {interval: POLL_INITIAL_MS, errors: 0};
        }
        return _pollState[jobId];
    }

    function _clearState(jobId) {
        delete _pollState[jobId];
    }

    function pollJob(channel, jobId) {
        var state = _initState(jobId);
        var url = '/settings/channels/' + encodeURIComponent(channel)
                + '/bind/' + encodeURIComponent(jobId);
        fetchJson(url, {credentials: 'same-origin'})
            .then(function (data) {
                if (!data) return;
                // Reset error counter on any successful response.
                state.errors = 0;
                var renderedCount = parseInt(
                    document.getElementById('bind-log-' + channel)
                        .getAttribute('data-rendered') || '0', 10);
                var events = data.events || [];
                for (var i = renderedCount; i < events.length; i++) {
                    appendLog(channel, describeEvent(events[i]));
                }
                document.getElementById('bind-log-' + channel)
                    .setAttribute('data-rendered', String(events.length));

                if (data.status === 'done') {
                    setBadge(channel, 'done');
                    showRefreshNotice(channel);
                    appendLog(
                        channel,
                        '已取得授权，请刷新页面查看最新状态',
                        {scroll: true}
                    );
                    _clearState(jobId);
                } else if (data.status === 'failed') {
                    setBadge(channel, 'failed');
                    if (data.error_message) appendLog(channel, data.error_message);
                    _clearState(jobId);
                } else {
                    setBadge(channel, 'running');
                    var delay = state.interval;
                    // Compute next interval AFTER scheduling current one,
                    // so the first re-poll uses POLL_INITIAL_MS.
                    state.interval = Math.min(
                        state.interval * POLL_BACKOFF_FACTOR, POLL_MAX_MS);
                    setTimeout(function () { pollJob(channel, jobId); }, delay);
                }
            })
            .catch(function (err) {
                state.errors += 1;
                if (state.errors >= POLL_MAX_CONSECUTIVE_ERRORS) {
                    appendLog(channel,
                        '轮询连续失败 ' + state.errors + ' 次，停止轮询：' + err);
                    setBadge(channel, 'failed');
                    _clearState(jobId);
                    return;
                }
                appendLog(channel,
                    '轮询出错 (重试 ' + state.errors + '/'
                    + POLL_MAX_CONSECUTIVE_ERRORS + ')：' + err);
                // Retry with backoff on transient errors.
                var delay = state.interval;
                state.interval = Math.min(
                    state.interval * POLL_BACKOFF_FACTOR, POLL_MAX_MS);
                setTimeout(function () { pollJob(channel, jobId); }, delay);
            });
    }

    function startBind(channel) {
        try { sessionStorage.setItem('bind:lastChannel', channel); } catch (e) {}
        var log = document.getElementById('bind-log-' + channel);
        if (log) {
            log.innerHTML = '';
            log.setAttribute('data-rendered', '0');
        }
        setBadge(channel, 'running');

        var body = new FormData();
        body.append('csrf_token', getCsrfToken());

        fetch('/settings/channels/' + encodeURIComponent(channel) + '/bind', {
            method: 'POST',
            credentials: 'same-origin',
            body: body
        })
            .then(function (r) {
                var ct = r.headers.get('content-type') || '';
                if (!ct.includes('application/json')) {
                    throw new Error('服务器返回非 JSON 响应 (HTTP ' + r.status + ')');
                }
                return r.json().then(function (j) {
                    return {status: r.status, body: j};
                });
            })
            .then(function (resp) {
                if (resp.status !== 200 || !resp.body || !resp.body.job_id) {
                    setBadge(channel, 'failed');
                    appendLog(channel, '启动失败：' + (resp.body && resp.body.error || resp.status));
                    return;
                }
                pollJob(channel, resp.body.job_id);
            })
            .catch(function (err) {
                setBadge(channel, 'failed');
                appendLog(channel, '请求出错：' + err);
            });
    }

    function init() {
        var buttons = document.querySelectorAll('.bind-channel-btn');
        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            (function (button) {
                button.addEventListener('click', function () {
                    var channel = button.getAttribute('data-channel');
                    if (channel) startBind(channel);
                });
            })(btn);
        }

        // Manual-fallback login URL: copy-to-clipboard handler.
        // Falls back to a one-shot ``document.execCommand('copy')`` against a
        // hidden textarea when the async Clipboard API is unavailable (older
        // browsers, insecure contexts).
        var copyButtons = document.querySelectorAll('.bind-copy-url-btn');
        for (var j = 0; j < copyButtons.length; j++) {
            var cbtn = copyButtons[j];
            (function (button) {
                button.addEventListener('click', function () {
                    var url = button.getAttribute('data-url') || '';
                    if (!url) return;
                    var done = function () {
                        var target = button.getAttribute('data-clipboard-target');
                        var ch = '';
                        if (target) {
                            var m = target.match(/#bind-login-url-(.+)$/);
                            if (m) ch = m[1];
                        }
                        var toast = ch ? document.getElementById('bind-copy-toast-' + ch) : null;
                        if (toast) {
                            toast.style.display = 'inline';
                            setTimeout(function () { toast.style.display = 'none'; }, 1500);
                        }
                    };
                    if (navigator.clipboard && navigator.clipboard.writeText) {
                        navigator.clipboard.writeText(url).then(done, function () {
                            fallbackCopy(url, done);
                        });
                    } else {
                        fallbackCopy(url, done);
                    }
                });
            })(cbtn);
        }
    }

    function fallbackCopy(text, onDone) {
        try {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.top = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            if (onDone) onDone();
        } catch (_) { /* swallow */ }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
