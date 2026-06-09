// Vitest tests for webui_app/static/js/lib/dom.js
// Run from webui_app/static/js/ with: npx vitest run
import { describe, it, expect, beforeEach } from 'vitest';
import { esc, qs, qsa, on, delegate, renderBadge } from '../../webui_app/static/js/lib/dom.js';

describe('lib/dom.esc()', () => {
  it('escapes all 5 chars incl single-quote', () => {
    // The 5-char superset (& < > " ') is what channel-binding needs.
    // Build input via String.fromCharCode to avoid template-literal escape pitfalls.
    const input = [39, 34, 60, 62, 38].map((c) => String.fromCharCode(c)).join('');
    const result = esc(input);
    // Each input char is replaced with a 5/6-char entity; result has 5 entities.
    expect(result).toContain(String.fromCharCode(38, 35, 51, 57, 59));  // &#39;
    expect(result).toContain(String.fromCharCode(38, 113, 117, 111, 116, 59));  // "
    expect(result).toContain(String.fromCharCode(38, 108, 116, 59));  // <
    expect(result).toContain(String.fromCharCode(38, 103, 116, 59));  // >
    expect(result).toContain(String.fromCharCode(38, 97, 109, 112, 59));  // &
  });

  it('neutralises an XSS <img> payload', () => {
    const out = esc('"><img src=x onerror=alert(1)>');
    expect(out).not.toMatch(/<img/i);
  });

  it('renders nullish as empty string', () => {
    expect(esc(null)).toBe('');
    expect(esc(undefined)).toBe('');
  });

  it('passes through safe text unchanged', () => {
    expect(esc('hello world')).toBe('hello world');
  });

  it('coerces numbers to strings', () => {
    expect(esc(42)).toBe('42');
  });
});

describe('lib/dom.qs/qsa', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="a"></div><div class="b"></div><div class="b"></div>';
  });

  it('qs returns first match', () => {
    expect(qs('#a')).toBe(document.getElementById('a'));
  });

  it('qsa returns all matches as array', () => {
    const els = qsa('.b');
    expect(els).toHaveLength(2);
  });
});

describe('lib/dom.on / delegate', () => {
  it('on() attaches a listener that fires on event', () => {
    let called = 0;
    const el = document.createElement('button');
    on(el, 'click', () => called++);
    el.dispatchEvent(new Event('click'));
    expect(called).toBe(1);
  });

  it('on() returns an unbind function', () => {
    let called = 0;
    const el = document.createElement('button');
    const off = on(el, 'click', () => called++);
    off();
    el.dispatchEvent(new Event('click'));
    expect(called).toBe(0);
  });

  it('delegate() only fires for matching selector descendants', () => {
    let matched = 0;
    const root = document.createElement('div');
    root.innerHTML = '<button class="hit">a</button><button class="miss">b</button>';
    delegate(root, 'click', '.hit', () => matched++);
    root.querySelector('.hit').dispatchEvent(new Event('click', { bubbles: true }));
    root.querySelector('.miss').dispatchEvent(new Event('click', { bubbles: true }));
    expect(matched).toBe(1);
  });
});

describe('lib/dom.renderBadge', () => {
  it('builds a span.badge with the given text', () => {
    const el = renderBadge('已綁定', 'bg-success');
    expect(el.tagName).toBe('SPAN');
    expect(el.className).toContain('badge');
    expect(el.className).toContain('bg-success');
    expect(el.textContent).toBe('已綁定');
  });

  it('escapes XSS in the badge text', () => {
    const el = renderBadge('"><script>alert(1)</script>');
    // renderBadge uses textContent, not innerHTML — no live script.
    expect(el.textContent).toBe('"><script>alert(1)</script>');
    expect(el.innerHTML).not.toContain('<script>');
  });
});
