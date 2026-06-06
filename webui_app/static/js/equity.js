// Equity ledger page entry — native ES module (Plan 2026-06-01-007 Unit 5).
//
// Ported from the inline <script> in equity_ledger.html. Three adaptations:
//   - server data now arrives via window.__equityLedgerBootstrap (read once),
//     because an external module cannot read the Jinja {{ rows|tojson }} context;
//   - the local esc() is replaced by the shared lib/dom.js esc() (5-char superset);
//   - the CSRF token is read per-call via readCsrf() (was a module-const).
// The page already used addEventListener (no inline on*), so behaviour is preserved.

import { esc, on, qsa } from './lib/dom.js';
import { readCsrf } from './lib/api.js';

const BOOT = window.__equityLedgerBootstrap || {};
const ROWS = BOOT.rows || [];
const EXACT_THRESHOLD = BOOT.exact_match_threshold;
const STALE_DAYS = BOOT.stale_days;
const LIVE_RANK = { failed: 3, stale: 2, live: 1, unverified: 0 };
let sortKey = 'live_dofollow';
let sortDir = 1; // weakest first

function truncMiddle(s, n = 48) {
  if (s.length <= n) return s;
  const head = Math.ceil(n * 0.6);
  const tail = n - head - 1;
  return s.slice(0, head) + '…' + s.slice(s.length - tail);
}

function livenessBadge(r) {
  const map = {
    live: ['text-bg-success', 'bi-check-circle', 'live'],
    stale: ['text-bg-warning', 'bi-clock-history', 'stale'],
    failed: ['text-bg-danger', 'bi-x-octagon', 'failed'],
    unverified: ['text-bg-secondary', 'bi-question-circle', 'unverified'],
  };
  const [cls, icon, label] = map[r.liveness] || map.unverified;
  const date = r.liveness_verified_at
    ? ` <span class="text-muted">${esc(r.liveness_verified_at.slice(0, 10))}</span>` : '';
  const qual = r.liveness_row_level ? ' <i class="bi bi-info-circle" title="row-level evidence"></i>' : '';
  return `<span class="badge ${cls}"><i class="bi ${icon}"></i> ${esc(label)}</span>${date}${qual}`;
}

function exactCell(r) {
  if (!r.has_anchor_data) return '<span class="text-muted">—</span>';
  const pct = (r.exact_match_pct * 100).toFixed(0) + '%';
  const over = EXACT_THRESHOLD != null && r.exact_match_pct > EXACT_THRESHOLD;
  return over ? `<span class="text-danger fw-bold">${pct}</span>` : pct;
}

function liveLabel(r) {
  return (r.live_links === 0 && r.liveness === 'unverified')
    ? `<span class="text-muted">— / ${r.total_links}</span>`
    : `${r.live_links} / ${r.total_links}`;
}

function passesFilter(r, lf, uf) {
  if (uf && !r.target_url.toLowerCase().includes(uf)) return false;
  if (lf === 'all') return true;
  if (lf === 'has-failed') return r.liveness === 'failed';
  if (lf === 'has-stale') return r.liveness === 'stale';
  return r.liveness === lf;
}

function render() {
  const lf = document.getElementById('livenessFilter').value;
  const uf = document.getElementById('urlFilter').value.trim().toLowerCase();
  const rows = ROWS.filter((r) => passesFilter(r, lf, uf));
  rows.sort((a, b) => {
    let av = a[sortKey];
    let bv = b[sortKey];
    if (sortKey === 'liveness') { av = LIVE_RANK[av]; bv = LIVE_RANK[bv]; }
    if (av < bv) return -1 * sortDir;
    if (av > bv) return 1 * sortDir;
    return a.target_url < b.target_url ? -1 : 1;
  });

  const body = document.getElementById('ledgerBody');
  const empty = document.getElementById('emptyState');
  body.innerHTML = '';
  if (ROWS.length === 0) {
    empty.textContent = 'No target pages yet — publish backlinks to populate the ledger.';
    empty.classList.remove('d-none');
    return;
  }
  if (rows.length === 0) {
    empty.textContent = 'No targets match the current filter.';
    empty.classList.remove('d-none');
    return;
  }
  empty.classList.add('d-none');

  for (const r of rows) {
    const d = r.dofollow;
    const tr = document.createElement('tr');
    if (r.live_dofollow === 0) tr.className = 'row-weak';
    tr.innerHTML =
      `<td><button class="btn btn-sm btn-link p-0 expand" aria-expanded="false" title="Details"><i class="bi bi-chevron-right"></i></button></td>` +
      `<td class="target-cell" title="${esc(r.target_url)}">${esc(truncMiddle(r.target_url))}</td>` +
      `<td class="text-end">${liveLabel(r)}</td>` +
      `<td class="text-end fw-semibold">${r.live_dofollow}</td>` +
      `<td class="text-end small">${d.dofollow}<span class="text-muted">/</span>${d.uncertain}<span class="text-muted">/</span>${d.nofollow}<span class="text-muted">/</span>${d.unknown}</td>` +
      `<td class="text-end">${exactCell(r)}</td>` +
      `<td class="text-end">${r.platform_count}</td>` +
      `<td>${livenessBadge(r)}</td>` +
      `<td><button class="btn btn-sm btn-outline-primary recheck" ${r.history_item_ids.length ? '' : 'disabled'}>Recheck</button></td>`;
    body.appendChild(tr);

    const detail = document.createElement('tr');
    detail.className = 'detail-row d-none';
    detail.innerHTML =
      `<td></td><td colspan="8"><table class="matrix"><tr>` +
      `<th>dofollow</th><td>${d.dofollow}</td>` +
      `<th>uncertain</th><td>${d.uncertain}</td>` +
      `<th>nofollow</th><td>${d.nofollow} (high ${d.nofollow_high} / low ${d.nofollow_low})</td>` +
      `<th>unknown</th><td>${d.unknown}</td>` +
      `<th>platforms</th><td>${esc((r.platforms || []).join(', ')) || '—'}</td>` +
      `</tr></table></td>`;
    body.appendChild(detail);

    on(tr.querySelector('.expand'), 'click', (e) => {
      const btn = e.currentTarget;
      const open = detail.classList.toggle('d-none') === false;
      btn.setAttribute('aria-expanded', String(open));
      btn.querySelector('i').className = open ? 'bi bi-chevron-down' : 'bi bi-chevron-right';
    });
    on(tr.querySelector('.recheck'), 'click', () => recheck(r, tr));
  }
}

let recheckBusy = false;
async function recheck(r, tr) {
  if (recheckBusy) return;
  recheckBusy = true;
  const btn = tr.querySelector('.recheck');
  const status = document.getElementById('recheckStatus');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
  status.textContent = `Rechecking ${r.history_item_ids.length} row(s) touching ${r.target_url}…`;
  try {
    const resp = await fetch('/ce:equity-ledger/recheck', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': readCsrf() },
      body: JSON.stringify({ target_url: r.target_url, stale_days: STALE_DAYS }),
    });
    const ct = resp.headers.get('content-type') || '';
    if (!resp.ok || !ct.includes('application/json')) throw new Error('recheck failed (' + resp.status + ')');
    const data = await resp.json();
    Object.assign(r, data.row);
    const i = ROWS.findIndex((x) => x.target_url === r.target_url);
    if (i >= 0) Object.assign(ROWS[i], data.row);
    rerenderRowInPlace(r, tr);
    status.textContent = `${r.target_url}: ${data.summary} (rechecked ${new Date().toLocaleTimeString()})`;
  } catch (err) {
    status.textContent = `${r.target_url}: ${err.message}`;
    btn.disabled = false;
    btn.textContent = 'Recheck';
  } finally {
    recheckBusy = false;
  }
}

function rerenderRowInPlace(r, tr) {
  const cells = tr.children;
  cells[2].innerHTML = liveLabel(r);
  cells[3].textContent = r.live_dofollow;
  cells[7].innerHTML = livenessBadge(r);
  tr.classList.toggle('row-weak', r.live_dofollow === 0);
  const btn = tr.querySelector('.recheck');
  btn.disabled = r.history_item_ids.length === 0;
  btn.textContent = 'Recheck';
}

function boot() {
  qsa('th[data-sort]').forEach((th) => on(th, 'click', () => {
    const k = th.dataset.sort;
    sortDir = (sortKey === k) ? -sortDir : 1;
    sortKey = k;
    qsa('th[data-sort]').forEach((h) => h.removeAttribute('aria-sort'));
    th.setAttribute('aria-sort', sortDir === 1 ? 'ascending' : 'descending');
    render();
  }));
  const lf = document.getElementById('livenessFilter');
  const uf = document.getElementById('urlFilter');
  if (lf) on(lf, 'change', render);
  if (uf) on(uf, 'input', render);
  render();
}

if (document.readyState === 'loading') on(document, 'DOMContentLoaded', boot); else boot();
