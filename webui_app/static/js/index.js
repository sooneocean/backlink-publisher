// Index page entry — native ES module (Plan 2026-06-01-007 Unit 6).
//
// Replaces the bare top-level index_main.js. All cross-script window globals are
// internalized: __rewireBulkSelect (module var), window.urlDerive (imported),
// window.fetchJson (via lib/api). The 7 config-form fns live in lib/profiles.js.
// Inline on* handlers became data-action; the inline urlDerive-consumer and the
// mode_toggle/url_derive auto-inits are driven from here.

import { on, delegate, qsa } from './lib/dom.js';
import { createConfigForm } from './lib/profiles.js';
import { bindPasteInput } from './url_derive.js';
import { initModeToggle } from './mode_toggle.js';

const BOOT = window.__indexBootstrap || {};
const PLATFORM_SLUGS = BOOT.platform_slugs || [];
const cf = createConfigForm({ plansData: BOOT.plans_list || [], profiles: BOOT.profiles || [] });

function loadHistory(id) {
  window.location.href = '/ce:history?id=' + id;
}

// ── data-action wiring ───────────────────────────────────────────
const CLICK_ACTIONS = {
  'load-history': (e, el) => loadHistory(el.dataset.id),
  'save-profile': () => cf.saveProfilePrompt(),
  'toggle-editor': (e, el) => cf.toggleEditor(el.dataset.idx),
  'save-edit': (e, el) => cf.saveEdit(el.dataset.idx),
  'cancel-edit': (e, el) => cf.cancelEdit(el.dataset.idx, el.dataset.original),
  'append-tag': (e, el) => {
    const tagsEl = document.getElementsByName('custom_tags')[0];
    if (tagsEl) tagsEl.value += el.dataset.tag + ',';
  },
};
const CHANGE_ACTIONS = {
  'load-profile': (e, el) => cf.loadProfile(el.value),
  'load-batch-profile': (e, el) => cf.loadBatchProfile(el.value),
};
const KEYUP_ACTIONS = {
  'mark-dirty': (e, el) => cf.markDirty(el.dataset.idx),
};

function _initActions() {
  delegate(document, 'click', '[data-action]', (e, el) => {
    const h = CLICK_ACTIONS[el.dataset.action];
    if (h) { e.preventDefault(); h(e, el); }
  });
  delegate(document, 'change', '[data-action]', (e, el) => {
    const h = CHANGE_ACTIONS[el.dataset.action];
    if (h) h(e, el);
  });
  delegate(document, 'keyup', '[data-action]', (e, el) => {
    const h = KEYUP_ACTIONS[el.dataset.action];
    if (h) h(e, el);
  });
  // `return confirm(...)` guards → preventDefault on cancel (forms + buttons).
  delegate(document, 'submit', 'form[data-confirm]', (e, form) => {
    if (!confirm(form.dataset.confirm)) e.preventDefault();
  });
  delegate(document, 'click', 'button[data-confirm]', (e, btn) => {
    if (!confirm(btn.dataset.confirm)) e.preventDefault();
  });
}

// ── Loading overlay ──────────────────────────────────────────────
function _initLoadingOverlay() {
  const MSGS = {
    '/ce:plan': { text: '分析网址中…', sub: '正在抓取页面元数据' },
    '/ce:generate': { text: 'AI 生成文章中…', sub: '调用 AI 生成外链文章，约需 30–60 秒' },
    '/ce:validate': { text: '验证内容中…', sub: '检查外链格式与内容合规性' },
    '/ce:publish': { text: '发布中…', sub: '正在发布到目标平台，请勿关闭页面' },
    '/ce:publish-real': { text: '正式发布中…', sub: '正在写入平台，请勿关闭页面' },
    '/ce:batch': { text: '批量发布中…', sub: '正在逐篇生成并发布，每篇约 30–60 秒，请勿关闭页面' },
    '/checkpoint/resume': { text: '恢复发布中…', sub: '正在处理未完成的发布任务，可能需要数分钟，请勿关闭页面' },
  };
  on(document, 'submit', (e) => {
    const form = e.target;
    const action = ((form.getAttribute && form.getAttribute('action')) || '').split('?')[0];
    if (['/ce:clear', '/ce:history/delete', '/ce:history/update-status'].includes(action)) return;
    const msg = MSGS[action] || { text: '处理中…', sub: '请稍候' };
    const t = document.getElementById('_loadingText');
    const s = document.getElementById('_loadingSubtext');
    const overlay = document.getElementById('_loadingOverlay');
    if (t) t.textContent = msg.text;
    if (s) s.textContent = msg.sub;
    if (overlay) overlay.style.display = 'flex';
    form.querySelectorAll('[type="submit"]').forEach((btn) => { btn.disabled = true; });
  });
}

// ── History filter (status × platform) — calls rewireBulkSelect ──
let rewireBulkSelect = null;

function _initHistoryFilter() {
  const cardBody = document.getElementById('historyCardBody');
  if (!cardBody) return;
  const items = cardBody.querySelectorAll('.history-item[data-status]');
  if (!items.length) return;
  const chips = cardBody.querySelectorAll('.filter-chip');
  const emptyFiltered = document.getElementById('historyEmptyFiltered');
  let currentStatus = 'all';
  let currentPlatform = 'all';

  function applyFilter() {
    let visible = 0;
    items.forEach((item) => {
      const matchStatus = (currentStatus === 'all') || (item.dataset.status === currentStatus);
      const matchPlatform = (currentPlatform === 'all') || (item.dataset.platform === currentPlatform);
      if (matchStatus && matchPlatform) { item.style.display = ''; visible++; } else { item.style.display = 'none'; }
    });
    if (emptyFiltered) emptyFiltered.style.display = visible === 0 ? '' : 'none';
  }

  function initCounts() {
    const counts = {
      status: { all: 0, drafted: 0, published: 0, failed: 0, other: 0 },
      platform: Object.assign({ all: 0 }, Object.fromEntries(PLATFORM_SLUGS.map((s) => [s, 0])), { other: 0 }),
    };
    items.forEach((item) => {
      counts.status.all++;
      counts.platform.all++;
      const st = item.dataset.status;
      const pf = item.dataset.platform;
      if (counts.status[st] !== undefined) counts.status[st]++;
      if (counts.platform[pf] !== undefined) counts.platform[pf]++;
    });
    let unverifiedCount = 0;
    items.forEach((item) => { if (item.dataset.status === 'unverified') unverifiedCount++; });
    counts.status.unverified = unverifiedCount;
    chips.forEach((chip) => {
      const group = chip.dataset.filterGroup;
      const value = chip.dataset.filterValue;
      const span = chip.querySelector('.chip-count');
      if (span && counts[group] && counts[group][value] !== undefined) span.textContent = counts[group][value];
    });
  }

  chips.forEach((chip) => {
    on(chip, 'click', () => {
      const group = chip.dataset.filterGroup;
      const value = chip.dataset.filterValue;
      if (group === 'status') currentStatus = value;
      else if (group === 'platform') currentPlatform = value;
      cardBody.querySelectorAll('.filter-chip[data-filter-group="' + group + '"]').forEach((sib) => sib.classList.remove('active'));
      chip.classList.add('active');
      applyFilter();
      if (typeof rewireBulkSelect === 'function') rewireBulkSelect();
    });
  });

  initCounts();
  applyFilter();
}

// ── Bulk-select (defines rewireBulkSelect) + tooltips + img fallback ──
function _initBulkSelect() {
  function wireSection(rootId, selectAllId, checkboxClass, countLabelId, btnClass) {
    const root = document.getElementById(rootId);
    if (!root) return null;
    const selectAll = document.getElementById(selectAllId);
    const countLabel = document.getElementById(countLabelId);
    if (!selectAll) return null;
    const buttons = document.querySelectorAll('.' + btnClass);

    function visibleCheckboxes() {
      return Array.prototype.filter.call(root.querySelectorAll('.' + checkboxClass), (cb) => {
        const host = cb.closest('.history-item');
        return host && host.style.display !== 'none';
      });
    }
    function refresh() {
      const visible = visibleCheckboxes();
      const checked = visible.filter((cb) => cb.checked);
      if (countLabel) countLabel.textContent = '(' + checked.length + '/' + visible.length + ')';
      buttons.forEach((btn) => { btn.disabled = checked.length === 0; });
      if (visible.length === 0) { selectAll.indeterminate = false; selectAll.checked = false; }
      else if (checked.length === visible.length) { selectAll.indeterminate = false; selectAll.checked = true; }
      else if (checked.length === 0) { selectAll.indeterminate = false; selectAll.checked = false; }
      else { selectAll.indeterminate = true; }
    }
    on(selectAll, 'change', () => {
      const target = selectAll.checked;
      visibleCheckboxes().forEach((cb) => { cb.checked = target; });
      refresh();
    });
    on(document, 'change', (e) => {
      if (e.target.classList && e.target.classList.contains(checkboxClass)) refresh();
    });
    return refresh;
  }
  const refreshDraft = wireSection('draftCardBody', 'draftSelectAll', 'draft-bulk-select', 'draftSelectedCount', 'draft-bulk-btn');
  const refreshHistory = wireSection('historyCardBody', 'historySelectAll', 'history-bulk-select', 'historySelectedCount', 'history-bulk-btn');

  // Module-scoped (was window.__rewireBulkSelect) — history filter calls this.
  rewireBulkSelect = function () {
    if (refreshHistory) {
      const root = document.getElementById('historyCardBody');
      if (root) {
        root.querySelectorAll('.history-bulk-select').forEach((cb) => {
          const host = cb.closest('.history-item');
          if (host && host.style.display === 'none' && cb.checked) cb.checked = false;
        });
      }
      refreshHistory();
    }
    if (refreshDraft) refreshDraft();
  };
  if (refreshDraft) refreshDraft();
  if (refreshHistory) refreshHistory();

  // Bootstrap tooltips + broken-image fallback.
  if (window.bootstrap) {
    qsa('[data-bs-toggle="tooltip"]').forEach((el) => new window.bootstrap.Tooltip(el));
  }
  qsa('.content-preview img').forEach((img) => {
    img.onerror = function () {
      img.style.display = 'none';
      const warn = document.createElement('div');
      warn.className = 'alert alert-secondary py-1 px-2 my-2 d-inline-block';
      warn.style.fontSize = '12px';
      warn.innerHTML = "<i class='bi bi-image-alt me-1'></i>封面图片加载失败";
      img.parentNode.insertBefore(warn, img.nextSibling);
    };
  });
}

// ── url-derive paste binding (was the index inline <script>) ─────
function _initUrlDerive() {
  const pasteEl = document.getElementById('derive_source');
  if (!pasteEl) return;
  bindPasteInput(
    pasteEl,
    {
      main: document.querySelector('input[name="main_url"]'),
      category: document.querySelector('input[name="category_url"]'),
      work: document.querySelector('input[name="work_url"]'),
    },
    {
      main: document.getElementById('status-main'),
      category: document.getElementById('status-category'),
      work: document.getElementById('status-work'),
    },
  );
}

// ── boot ─────────────────────────────────────────────────────────
function _boot() {
  _initActions();
  _initLoadingOverlay();
  _initHistoryFilter();
  _initBulkSelect();   // defines rewireBulkSelect — before filter clicks fire
  _initUrlDerive();
  initModeToggle();
}

if (document.readyState === 'loading') on(document, 'DOMContentLoaded', _boot); else _boot();
