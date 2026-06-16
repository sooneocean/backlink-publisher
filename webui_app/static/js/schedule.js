// /schedule — Plan 2026-05-29-001 Unit 2, front-end ESM.
import { fetchJson, readCsrf } from './lib/api.js';
import { esc } from './lib/dom.js';
import { on } from './lib/dom.js';

const scheduleList = document.getElementById('scheduleList');
if (!scheduleList) throw new Error('missing #scheduleList');

function renderRow(item) {
  const tr = document.createElement('tr');
  const scheduledAt = item.scheduled_at ? new Date(item.scheduled_at).toLocaleString('zh-CN') : '—';
  const createdAt = item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '—';
  const title = esc(item.title || '无标题');
  const target = esc(item.target_url || '');
  const platform = esc(item.platform || '');
  tr.innerHTML = `
    <td><span class="badge bg-secondary-subtle text-secondary">${platform}</span></td>
    <td>${title}</td>
    <td><a href="${target}" target="_blank" rel="noopener">${target || '—'}</a></td>
    <td>${esc(scheduledAt)}</td>
    <td>${esc(createdAt)}</td>
  `;
  return tr;
}

function renderEmptyScheduled() {
  const tr = document.createElement('tr');
  tr.className = 'placeholder-row';
  tr.innerHTML = '<td colspan="5" class="text-center py-4 text-muted">暂无计划发布</td>';
  return tr;
}

function renderLoading() {
  const tr = document.createElement('tr');
  tr.className = 'placeholder-row';
  tr.innerHTML = '<td colspan="5" class="text-center py-4 text-muted">正在加载……</td>';
  return tr;
}

async function loadScheduled() {
  const resp = await fetch('/api/scheduled', {
    headers: { 'Accept': 'application/json', 'X-CSRFToken': readCsrf() },
    cache: 'no-store',
  });
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  const payload = await resp.json();
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const fragment = document.createDocumentFragment();
  if (items.length === 0) {
    fragment.appendChild(renderEmptyScheduled());
  } else {
    for (const item of items) fragment.appendChild(renderRow(item));
  }
  scheduleList.innerHTML = '';
  scheduleList.appendChild(fragment);
}

function refreshList() {
  loadScheduled().catch((err) => {
    console.warn('load scheduled failed', err);
    const tr = document.createElement('tr');
    tr.className = 'placeholder-row';
    tr.innerHTML = '<td colspan="5" class="text-center py-4 text-danger">加载失败，请刷新</td>';
    scheduleList.innerHTML = '';
    scheduleList.appendChild(tr);
  });
}

on(document, 'visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshList();
});

refreshList();
setInterval(refreshList, 60_000);
