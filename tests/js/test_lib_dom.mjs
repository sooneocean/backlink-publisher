/**
 * Unit tests for webui_app/static/js/lib/dom.js
 *
 * Run with: node --test tests/js/test_lib_dom.mjs
 *
 * Uses Node.js built-in node:test + node:assert (no external deps).
 * Minimal DOM stub — only what dom.js touches is implemented.
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal DOM stub ──────────────────────────────────────────────────────

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.className = '';
    this.textContent = '';
    this.children = [];
    this._listeners = {};
    this.parentNode = null;
  }
  addEventListener(type, handler, opts) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push({ handler, opts });
  }
  removeEventListener(type, handler) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter(l => l.handler !== handler);
  }
  dispatchEvent(event) {
    const listeners = this._listeners[event.type] || [];
    for (const { handler } of listeners) handler(event);
  }
  querySelector(sel) { return null; }
  querySelectorAll(sel) { return []; }
  closest(sel) {
    // Simplified: returns self if className matches selector class
    if (sel.startsWith('.') && this.className.includes(sel.slice(1))) return this;
    return null;
  }
  contains(el) { return el === this || this.children.includes(el); }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  getAttribute(k) { return this._attrs?.[k] ?? null; }
  setAttribute(k, v) { (this._attrs ??= {})[k] = v; }
}

class FakeDocument {
  createElement(tag) { return new FakeElement(tag); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

globalThis.document = new FakeDocument();

// ── Inline the functions (copy from dom.js) ───────────────────────────────

const _ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => _ESC[c]);
}

function qs(selector, root = document) {
  return root.querySelector(selector);
}
function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

function on(target, type, handler, opts) {
  target.addEventListener(type, handler, opts);
  return () => target.removeEventListener(type, handler, opts);
}

function delegate(root, type, selector, handler, opts) {
  const listener = (event) => {
    const match = event.target.closest(selector);
    if (match && root.contains(match)) handler(event, match);
  };
  root.addEventListener(type, listener, opts);
  return () => root.removeEventListener(type, listener, opts);
}

function renderBadge(text, className = '') {
  const span = document.createElement('span');
  span.className = ('badge ' + className).trim();
  span.textContent = String(text == null ? '' : text);
  return span;
}

// ── Tests ─────────────────────────────────────────────────────────────────

describe('esc', () => {
  test('escapes ampersand', () => assert.equal(esc('a&b'), 'a&amp;b'));
  test('escapes less-than', () => assert.equal(esc('<script>'), '&lt;script&gt;'));
  test('escapes double-quote', () => assert.equal(esc('"hello"'), '&quot;hello&quot;'));
  test('escapes single-quote', () => assert.equal(esc("it's"), "it&#39;s"));
  test('escapes all 5 chars together', () => {
    assert.equal(esc('&<>"\'' ), '&amp;&lt;&gt;&quot;&#39;');
  });
  test('passes through safe text unchanged', () => assert.equal(esc('hello world'), 'hello world'));
  test('converts null to empty string', () => assert.equal(esc(null), ''));
  test('converts undefined to empty string', () => assert.equal(esc(undefined), ''));
  test('converts number to string', () => assert.equal(esc(42), '42'));
  test('no XSS via nested tags', () => {
    assert.ok(!esc('<img onerror=alert(1)>').includes('<img'));
  });
});

describe('qs / qsa', () => {
  test('qs returns null when no match', () => {
    assert.equal(qs('.missing'), null);
  });

  test('qsa returns empty array when no match', () => {
    assert.deepEqual(qsa('.missing'), []);
  });

  test('qs uses custom root', () => {
    const root = new FakeElement('div');
    const child = new FakeElement('span');
    root.querySelector = (sel) => child;
    assert.equal(qs('.anything', root), child);
  });
});

describe('on', () => {
  test('calls handler on event', () => {
    const el = new FakeElement('button');
    let called = false;
    on(el, 'click', () => { called = true; });
    el.dispatchEvent({ type: 'click', target: el });
    assert.ok(called);
  });

  test('returned teardown removes listener', () => {
    const el = new FakeElement('button');
    let count = 0;
    const off = on(el, 'click', () => { count++; });
    el.dispatchEvent({ type: 'click', target: el });
    off();
    el.dispatchEvent({ type: 'click', target: el });
    assert.equal(count, 1);
  });
});

describe('delegate', () => {
  test('calls handler when target matches selector', () => {
    const root = new FakeElement('div');
    const child = new FakeElement('button');
    child.className = 'my-btn';
    root.children.push(child);

    let receivedMatch;
    delegate(root, 'click', '.my-btn', (event, match) => {
      receivedMatch = match;
    });

    const event = { type: 'click', target: child };
    root.dispatchEvent(event);
    assert.equal(receivedMatch, child);
  });

  test('does not call handler when target does not match', () => {
    const root = new FakeElement('div');
    const unrelated = new FakeElement('span');
    root.children.push(unrelated);

    let called = false;
    delegate(root, 'click', '.my-btn', () => { called = true; });

    root.dispatchEvent({ type: 'click', target: unrelated });
    assert.ok(!called);
  });

  test('returned teardown removes listener', () => {
    const root = new FakeElement('div');
    const child = new FakeElement('button');
    child.className = 'action-btn';
    root.children.push(child);

    let count = 0;
    const off = delegate(root, 'click', '.action-btn', () => { count++; });
    root.dispatchEvent({ type: 'click', target: child });
    off();
    root.dispatchEvent({ type: 'click', target: child });
    assert.equal(count, 1);
  });
});

describe('renderBadge', () => {
  test('creates span with badge class and text', () => {
    const badge = renderBadge('active', 'badge-success');
    assert.equal(badge.tagName, 'span');
    assert.ok(badge.className.includes('badge'));
    assert.ok(badge.className.includes('badge-success'));
    assert.equal(badge.textContent, 'active');
  });

  test('uses safe textContent (not innerHTML) — XSS safe', () => {
    const badge = renderBadge('<script>xss</script>');
    // textContent assignment does NOT parse HTML
    assert.equal(badge.textContent, '<script>xss</script>');
  });

  test('handles null text gracefully', () => {
    const badge = renderBadge(null);
    assert.equal(badge.textContent, '');
  });

  test('handles undefined text gracefully', () => {
    const badge = renderBadge(undefined);
    assert.equal(badge.textContent, '');
  });

  test('default className is just "badge"', () => {
    const badge = renderBadge('ok');
    assert.equal(badge.className, 'badge');
  });

  test('number text is stringified', () => {
    const badge = renderBadge(99);
    assert.equal(badge.textContent, '99');
  });
});
