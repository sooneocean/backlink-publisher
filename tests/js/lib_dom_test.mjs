// Node-level test for lib/dom.js pure helpers.
// Run:  node --test tests/js/lib_dom_test.mjs
// Uses node:test + node:assert (zero dependencies).
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { esc } from '../../webui_app/static/js/lib/dom.js';

describe('esc() — HTML 5-char superset escape', () => {
  it('escapes all 5 dangerous characters', () => {
    assert.equal(esc('\'"<>&'), '&#39;&quot;&lt;&gt;&amp;');
  });

  it('neutralises XSS <img> payload', () => {
    const payload = esc('"><img src=x onerror=alert(1)>');
    // esc() destroys HTML structure by escaping < > " '
    assert.ok(!payload.includes('<img'));            // < becomes &lt;
    assert.ok(!payload.includes('">'));              // acts as raw text
    assert.ok(payload.includes('&lt;img'));           // escaped tag present as text
    assert.ok(payload.includes('&gt;'));              // trailing > escaped
    // src= and alert remain as literal text — safe because structure is broken
    assert.ok(!payload.includes('<img'));             // no executable HTML
  });

  it('neutralises XSS <script> payload', () => {
    const payload = esc('<script>alert(1)</script>');
    assert.ok(!payload.includes('<script>'));
  });

  it('handles safe strings unchanged', () => {
    assert.equal(esc('hello world'), 'hello world');
    assert.equal(esc(''), '');
    assert.equal(esc('42'), '42');
    assert.equal(esc('a normal sentence with numbers 123'), 'a normal sentence with numbers 123');
  });

  it('handles nullish inputs as empty string', () => {
    assert.equal(esc(null), '');
    assert.equal(esc(undefined), '');
  });

  it('escapes only HTML metacharacters, leaves others', () => {
    // Amphersand inside an already-escaped entity should not double-escape.
    // (This is NOT idempotent by design — if someone passes &amp; through esc()
    // it becomes &amp;amp;. The contract is: esc() is for untrusted raw text.)
    // Test: & becomes &amp;
    assert.equal(esc('AT&T'), 'AT&amp;T');
    assert.equal(esc('price < 100'), 'price &lt; 100');
    assert.equal(esc('"quote"'), '&quot;quote&quot;');
    assert.equal(esc("single'quote"), 'single&#39;quote');
  });

  it('escapes double-quote for attribute context', () => {
    assert.equal(esc('class="foo"'), 'class=&quot;foo&quot;');
  });

  it('escapes single-quote for attribute context', () => {
    assert.equal(esc("class='foo'"), 'class=&#39;foo&#39;');
  });

  it('preserves non-ASCII and Unicode characters', () => {
    assert.equal(esc('中文'), '中文');
    assert.equal(esc('日本語'), '日本語');
    assert.equal(esc('한국어'), '한국어');
    assert.equal(esc('café'), 'café');
    assert.equal(esc('⟨⟩'), '⟨⟩');
  });

  it('handles numbers and booleans via String coercion', () => {
    // The function signature types value as string, but js doesn't enforce at runtime.
    // esc() uses String() coercion so it handles edge cases gracefully.
    assert.equal(esc(0), '0');
    assert.equal(esc(42), '42');
    assert.equal(esc(false), 'false');
    assert.equal(esc(true), 'true');
  });

  it('handles very long strings without throwing', () => {
    const long = 'x'.repeat(100000);
    const result = esc(long);
    assert.equal(result.length, 100000);
    assert.equal(result, long);
  });

  it('handles strings with only special chars', () => {
    assert.equal(esc('&'), '&amp;');
    assert.equal(esc('<'), '&lt;');
    assert.equal(esc('>'), '&gt;');
    assert.equal(esc('"'), '&quot;');
    assert.equal(esc("'"), '&#39;');
  });

  it('escapes mixed content correctly', () => {
    const mixed = '<b onclick="alert(\'xss\')">click</b>';
    const result = esc(mixed);
    assert.ok(!result.includes('<b'));
    assert.ok(result.includes('&lt;b'));
    assert.ok(result.includes('&gt;'));
    assert.ok(result.includes('&quot;'));
    assert.ok(result.includes('&#39;'));
  });
});
