// Pro Mode Copilot — global slide-out advisory panel (Plan U4, native ES module).
//
// Read-only and keyless: lazily GETs /copilot/advice (the deterministic
// in-process aggregator) on first open and renders the ranked, source-traceable
// list with freshness disclosure + per-tool status. Failures surface honestly
// (degraded banner + error state), never a false-green. No inline on* handlers
// (index.html's no-inline-handler test guards the included markup) — every
// listener is attached here. The LLM Q&A footer ships dark in v1 (U5/U6); this
// module never sends anything off-machine.

import { fetchJson } from './lib/api.js';
import { esc, on, qs, delegate } from './lib/dom.js';

const SEV_BADGE = {
  critical: 'text-bg-danger',
  warning: 'text-bg-warning',
  info: 'text-bg-secondary',
};
const SEV_LABEL = { critical: '严重', warning: '注意', info: '提示' };

// "Surface, don't decide": a source badge links to the originating tool's page
// where one exists, so the operator can go judge for themselves.
const SOURCE_ROUTES = {
  'equity-ledger': '/ce:equity-ledger',
  'canary': '/ce:health',
  'audit-state': '/ce:health',
  'cull-channels': '/ce:health',
};

let loaded = false;
let loading = false;

function panel() { return qs('#copilotPanel'); }

function show(id, visible) {
  const el = qs('#' + id);
  if (el) el.classList.toggle('d-none', !visible);
}

function setOpen(open) {
  const p = panel();
  const toggle = qs('#copilotToggle');
  if (!p) return;
  p.classList.toggle('copilot-panel--open', open);
  p.setAttribute('aria-hidden', String(!open));
  if (toggle) toggle.setAttribute('aria-expanded', String(open));
  if (open && !loaded && !loading) loadAdvice();
}

function freshnessBadge(f) {
  if (!f) return '';
  if (f.kind === 'live') {
    return '<span class="copilot-fresh copilot-fresh--live">实时</span>';
  }
  // cached: disclose "measured on <date>" (v1 = disclosure, not decay).
  const asOf = f.as_of ? esc(f.as_of.slice(0, 10)) : '未知';
  return '<span class="copilot-fresh copilot-fresh--cached" title="缓存数据，测量于 '
    + asOf + '">缓存 · ' + asOf + '</span>';
}

function findingRow(f) {
  const sevClass = SEV_BADGE[f.severity] || SEV_BADGE.info;
  const sevLabel = SEV_LABEL[f.severity] || f.severity;
  return (
    '<li class="copilot-finding copilot-finding--' + esc(f.severity) + '">'
    + '<div class="copilot-finding__head">'
    + '<span class="copilot-finding__prio">#' + esc(f.priority) + '</span>'
    + '<span class="badge ' + sevClass + '">' + esc(sevLabel) + '</span>'
    + freshnessBadge(f.freshness)
    + '</div>'
    + '<p class="copilot-finding__summary">' + esc(f.summary) + '</p>'
    + '<div class="copilot-finding__meta">'
    + '<span class="copilot-src" data-action="copilot-source" data-tool="' + esc(f.source_tool) + '"'
    + ' title="' + esc(f.source_ref) + '">来源：' + esc(f.source_tool) + '</span>'
    + '</div>'
    + '</li>'
  );
}

function toolStatusRow(t) {
  const cls = t.ok ? 'copilot-tool--ok' : 'copilot-tool--err';
  const code = t.error_code ? ' (' + esc(t.error_code) + ')' : '';
  const label = t.ok ? (t.outcome === 'no_emit' ? '正常 · 无问题' : '正常') : '读取失败';
  return '<li class="' + cls + '"><span class="copilot-tool__name">' + esc(t.tool)
    + '</span> · ' + esc(label) + code + '</li>';
}

function render(data) {
  const findings = data.findings || [];
  const tools = data.per_tool_status || [];

  const degraded = qs('#copilotDegraded');
  if (data.degraded) {
    const failed = tools.filter((t) => !t.ok).map((t) => t.tool);
    degraded.textContent = '部分数据来源不可用（' + (failed.join('、') || '未知')
      + '），以下建议可能不完整。';
    degraded.classList.remove('d-none');
  } else {
    degraded.classList.add('d-none');
  }

  const list = qs('#copilotFindings');
  if (findings.length === 0) {
    list.innerHTML = '';
    show('copilotEmpty', true);
  } else {
    show('copilotEmpty', false);
    list.innerHTML = findings.map(findingRow).join('');
  }

  const ts = qs('#copilotToolStatus');
  if (tools.length) {
    qs('#copilotToolList').innerHTML = tools.map(toolStatusRow).join('');
    ts.classList.remove('d-none');
  } else {
    ts.classList.add('d-none');
  }

  const badge = qs('#copilotBadge');
  if (badge) {
    const critical = findings.filter((f) => f.severity === 'critical').length;
    badge.textContent = String(findings.length);
    badge.classList.toggle('d-none', findings.length === 0);
    badge.classList.toggle('copilot-fab__count--critical', critical > 0);
  }
}

async function loadAdvice() {
  if (loading) return;
  loading = true;
  show('copilotError', false);
  show('copilotEmpty', false);
  show('copilotLoading', true);
  const p = panel();
  const base = (p && p.dataset.adviceUrl) || '/copilot/advice';
  const url = base + '?page=' + encodeURIComponent(location.pathname);
  try {
    const data = await fetchJson(url);
    render(data);
    loaded = true;
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    qs('#copilotErrorMsg').textContent = '无法加载建议：' + msg;
    show('copilotError', true);
  } finally {
    show('copilotLoading', false);
    loading = false;
  }
}

function onPanelAction(e, el) {
  const action = el.dataset.action;
  if (action === 'copilot-close') {
    setOpen(false);
  } else if (action === 'copilot-retry') {
    loaded = false;
    loadAdvice();
  } else if (action === 'copilot-source') {
    const route = SOURCE_ROUTES[el.dataset.tool];
    if (route) window.location.href = route;
  }
}

function boot() {
  const toggle = qs('#copilotToggle');
  const p = panel();
  if (!toggle || !p) return; // panel not on this page
  on(toggle, 'click', () => setOpen(!p.classList.contains('copilot-panel--open')));
  delegate(p, 'click', '[data-action]', onPanelAction);
  on(document, 'keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
}

// ── Q&A (U6) ──────────────────────────────────────────────────────────────────

function qaConvo() { return qs('#copilotQaConvo'); }
function qaInput() { return qs('#copilotQaInput'); }
function qaForm() { return qs('#copilotQaForm'); }

function addQaBubble(text, cls) {
  const convo = qaConvo();
  if (!convo) return;
  const bubble = document.createElement('div');
  bubble.className = cls;
  bubble.textContent = text;
  convo.appendChild(bubble);
  convo.scrollTop = convo.scrollHeight;
}

function setQaLoading(loading) {
  const input = qaInput();
  const btn = qs('#copilotQaSend');
  const existing = qs('.copilot-qa__loading');
  if (loading) {
    if (!existing) {
      const el = document.createElement('div');
      el.className = 'copilot-qa__loading';
      el.textContent = '思考中…';
      qaConvo().appendChild(el);
    }
    if (input) input.disabled = true;
    if (btn) btn.disabled = true;
  } else {
    if (existing) existing.remove();
    if (input) input.disabled = false;
    if (btn) btn.disabled = false;
  }
}

function showQaError(msg) {
  const el = qs('#copilotQaErr');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('d-none');
}

function hideQaError() {
  const el = qs('#copilotQaErr');
  if (el) el.classList.add('d-none');
}

async function submitQa(question) {
  hideQaError();
  addQaBubble(question, 'copilot-qa__q');
  setQaLoading(true);
  const input = qaInput();
  if (input) input.value = '';
  const greeting = qs('.copilot-qa__greeting');
  if (greeting) greeting.classList.add('d-none');

  try {
    const data = await postJson('/copilot/ask', { question });
    const answer = data.answer || '（空回复）';
    addQaBubble(answer, 'copilot-qa__a');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    showQaError('发送失败：' + msg);
  } finally {
    setQaLoading(false);
  }
}

function bootQa() {
  const form = qaForm();
  if (!form) return; // locked — no Q&A form on this page
  on(form, 'submit', (e) => {
    e.preventDefault();
    const input = qaInput();
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    submitQa(text);
  });
}

// ── Boot sequence ────────────────────────────────────────────────────────────

if (document.readyState === 'loading') on(document, 'DOMContentLoaded', () => { boot(); bootQa(); });
else { boot(); bootQa(); }
