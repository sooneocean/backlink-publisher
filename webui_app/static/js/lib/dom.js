// Shared DOM helpers (native ES module).
//
// Replaces the inline on* handlers (via event delegation) and consolidates the
// HTML-escaping that was hand-rolled (and divergent) across the page scripts.

// esc() — HTML-escape for interpolating untrusted text into innerHTML / attributes.
// Uses the 5-char SUPERSET (& < > " '): equity_ledger's esc() escaped the single
// quote, channel-binding.js's escapeHtml did NOT. The superset weakens neither
// consumer (settings_main.js had no escaping at all — routing through here is an
// upgrade). NEVER drop the single-quote escape.
const _ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => _ESC[c]);
}

// qs/qsa — terse query helpers scoped to an optional root.
export function qs(selector, root = document) {
  return root.querySelector(selector);
}
export function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

// on() — direct listener. delegate() — event delegation from a root, so a single
// listener handles current AND future matching nodes (replaces inline on* and
// the per-node rebinding the old scripts did). A synthetic click on a matching
// selector bubbles to a delegated root listener — preserving the
// channel-binding -> bind_channel `.bind-channel-btn` click() contract.
export function on(target, type, handler, opts) {
  target.addEventListener(type, handler, opts);
  return () => target.removeEventListener(type, handler, opts);
}
export function delegate(root, type, selector, handler, opts) {
  const listener = (event) => {
    const match = event.target.closest(selector);
    if (match && root.contains(match)) handler(event, match);
  };
  root.addEventListener(type, listener, opts);
  return () => root.removeEventListener(type, listener, opts);
}

// renderBadge() — build a single <span class="badge ..."> with ESCAPED text.
// Badge-only: it returns one element with text content, NOT a drop-in for
// multi-cell <td> HTML assembly (use esc() + your own nodes for that).
export function renderBadge(text, className = '') {
  const span = document.createElement('span');
  span.className = ('badge ' + className).trim();
  span.textContent = String(text == null ? '' : text);
  return span;
}
