/* Plan 012 Unit 5 — single/batch mode toggle.
 *
 * Replaces the 批量发布 nav tab with a toggle bar (单笔/批量). Both
 * toggle buttons use Bootstrap 5 tab API (data-bs-toggle="tab") so
 * clicking them activates #newPanel or #batchPanel via the same
 * mechanism the nav buttons used to use.
 *
 * Persistence: last-chosen mode is stored in localStorage under
 * `webui_mode_default`. On page load the saved mode is restored unless
 * the server-side hint `window.__batchTabHint` is true (batch flow
 * landed on /?batch_tab=true after batch submit).
 *
 * Priority chain for initial mode:
 *   server hint (__batchTabHint) > ?tab=batch URL param > localStorage > DEFAULT
 *
 * Plan 013 U1 additions:
 *   - URL stash: saves window.location.search to localStorage when switching
 *     TO single mode, so deep-links survive a round-trip through batch mode.
 *   - Mid-pipeline confirm: if window._plansData has entries, warn before
 *     switching to batch mode (avoids losing in-progress single pipeline).
 *   - ?tab=batch deep-link: honours `?tab=batch` URL param at load time.
 *   - Body class: adds mode-single / mode-batch to document.body for CSS scoping.
 *   - History nav hide: adds hide-history-nav class to body in batch mode.
 *
 * Gracefully degrades when localStorage is denied (private mode etc.).
 *
 * Plan 007 U6: native ES module — index.js imports and calls initModeToggle().
 */
'use strict';

    var STORAGE_KEY = 'webui_mode_default';
    var STASH_KEY   = 'webui_url_stash';
    var DEFAULT_MODE = 'single';

    // ── localStorage helpers ─────────────────────────────────────────────────

    function safeGetStored(key) {
        try {
            return window.localStorage.getItem(key || STORAGE_KEY);
        } catch (_) {
            return null;
        }
    }

    function safeSetStored(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (_) {
            /* ignore */
        }
    }

    // ── Visual sync ──────────────────────────────────────────────────────────

    function syncToggleVisual(activeMode) {
        var singleBtn = document.getElementById('mode-single-btn');
        var batchBtn  = document.getElementById('mode-batch-btn');
        if (!singleBtn || !batchBtn) return;
        singleBtn.classList.toggle('active', activeMode === 'single');
        batchBtn.classList.toggle('active', activeMode === 'batch');
    }

    // ── Body class scoping (Plan 013 U1 + U3) ───────────────────────────────

    function applyBodyModeClass(mode) {
        document.body.classList.toggle('mode-single', mode === 'single');
        document.body.classList.toggle('mode-batch',  mode === 'batch');
        // history nav visibility — hide in batch mode
        document.body.classList.toggle('hide-history-nav', mode === 'batch');
    }

    // ── URL stash (Plan 013 U1) ──────────────────────────────────────────────
    // When switching TO single mode, stash the current search string so a
    // subsequent page reload in single mode can restore the query params.
    // When switching TO batch mode we clear the stash.

    function stashOrClearUrl(targetMode) {
        if (targetMode === 'single') {
            var search = window.location.search;
            if (search) {
                safeSetStored(STASH_KEY, search);
            }
        } else {
            // switching to batch — clear stash so old params don't leak back
            safeSetStored(STASH_KEY, '');
        }
    }

    // ── Mid-pipeline confirm (Plan 013 U1) ───────────────────────────────────
    // Returns true if the switch should proceed, false if the user cancelled.
    // Only fires when switching TO batch with active plans in single pipeline.

    function confirmSwitchToBatch() {
        // _plansData is injected by the template (may be undefined on fresh page)
        var plans = (typeof window._plansData !== 'undefined') ? window._plansData : [];
        if (!Array.isArray(plans) || plans.length === 0) return true;
        return window.confirm(
            '当前单笔发布流程有 ' + plans.length + ' 篇待处理文章。\n' +
            '切换到批量模式将离开此流程，是否继续？'
        );
    }

    // ── Pane activation ──────────────────────────────────────────────────────

    function activatePane(mode) {
        var targetId = mode === 'batch' ? '#batchPanel' : '#newPanel';
        var trigger = document.querySelector(
            '.mode-toggle-btn[data-bs-target="' + targetId + '"]'
        );
        if (trigger && window.bootstrap && window.bootstrap.Tab) {
            window.bootstrap.Tab.getOrCreateInstance(trigger).show();
        }
        syncToggleVisual(mode);
        applyBodyModeClass(mode);
    }

    // ── Click persistence + confirm + stash ──────────────────────────────────

    function wireToggleClickPersistence() {
        document.querySelectorAll('.mode-toggle-btn').forEach(function (btn) {
            btn.addEventListener('shown.bs.tab', function (ev) {
                var mode = ev.target.dataset.mode;
                if (mode) {
                    safeSetStored(STORAGE_KEY, mode);
                    syncToggleVisual(mode);
                    applyBodyModeClass(mode);
                    stashOrClearUrl(mode);
                }
            });

            // Pre-confirm before batch switch (before the tab animation fires)
            btn.addEventListener('click', function (ev) {
                var mode = btn.dataset.mode;
                if (mode === 'batch' && !confirmSwitchToBatch()) {
                    ev.preventDefault();
                    ev.stopImmediatePropagation();
                }
            });
        });
    }

    // ── Initial mode determination (Plan 013 U1: adds ?tab deep-link) ────────
    // Priority: server hint > ?tab URL param > localStorage > DEFAULT

    function determineInitialMode() {
        // 1. Server hint takes precedence (batch_tab=true after /ce:batch POST)
        if (window.__batchTabHint === true) return 'batch';

        // 2. URL deep-link: ?tab=batch
        try {
            var tabParam = new URLSearchParams(window.location.search).get('tab');
            if (tabParam === 'batch') return 'batch';
            if (tabParam === 'single') return 'single';
        } catch (_) {
            /* URLSearchParams not available in very old browsers */
        }

        // 3. localStorage persistence
        var stored = safeGetStored(STORAGE_KEY);
        if (stored === 'single' || stored === 'batch') return stored;

        return DEFAULT_MODE;
    }

    // ── Init ─────────────────────────────────────────────────────────────────

    function init() {
        wireToggleClickPersistence();
        var initialMode = determineInitialMode();
        if (initialMode !== 'single') {
            // Only act when the desired mode differs from the template default
            // (#newPanel.active = single). Avoids a redundant tab activation.
            activatePane(initialMode);
        } else {
            syncToggleVisual('single');
            applyBodyModeClass('single');
        }
    }

// Exported; index.js calls initModeToggle() at boot (no self-init).
export { init as initModeToggle };
