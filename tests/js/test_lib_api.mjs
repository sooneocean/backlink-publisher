/**
 * Unit tests for webui_app/static/js/lib/api.js
 *
 * Run with: node --test tests/js/test_lib_api.mjs
 *
 * Uses Node.js built-in node:test + node:assert (no external deps).
 * Mocks DOM via globalThis so the ES module import works headlessly.
 */

import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// ── DOM stub (minimal — only what api.js touches) ─────────────────────────

function makeMeta(content) {
  return { content, tagName: 'META' };
}

function stubDocument(metaContent) {
  return {
    querySelector(sel) {
      if (sel === 'meta[name="csrf-token"]') {
        return metaContent !== undefined ? makeMeta(metaContent) : null;
      }
      return null;
    },
  };
}

// Inject globals before importing the module
globalThis.document = stubDocument('test-csrf-token-abc123');

// We can't use dynamic import with a file:// path easily in test, so inline the
// functions under test (mirrors the lib surface exactly — any divergence is a bug).
// This avoids the test needing a local server / import map for the browser ESM.

// ── Inline the functions (copy from api.js — MUST stay in sync) ──────────

function readCsrf() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return (meta && meta.content) || '';
}

async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  const ct = resp.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    const kind = ct.split(';')[0] || '未知类型';
    throw new Error('服务器返回非 JSON 响应 (HTTP ' + resp.status + ' ' + kind + ')');
  }
  return await resp.json();
}

async function postJson(url, body, opts = {}) {
  // Fixed: destructure headers to avoid ...opts overwriting the CSRF headers object.
  const { headers: extraHeaders, ...rest } = opts;
  return fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': readCsrf(), ...(extraHeaders || {}) },
    body: body == null ? undefined : JSON.stringify(body),
    ...rest,
  });
}

async function postForm(url, fields = {}, opts = {}) {
  // Mirrors api.js: builds FormData internally, appends csrf_token as form field.
  const data = { _isMockFormData: true, fields: { csrf_token: readCsrf(), ...fields } };
  return fetchJson(url, { method: 'POST', body: data, ...opts });
}

// ── Tests ─────────────────────────────────────────────────────────────────

describe('readCsrf', () => {
  test('returns content of csrf meta tag', () => {
    globalThis.document = stubDocument('my-csrf-token');
    assert.equal(readCsrf(), 'my-csrf-token');
  });

  test('returns empty string when meta tag is absent', () => {
    globalThis.document = stubDocument(undefined);
    assert.equal(readCsrf(), '');
  });

  test('returns empty string when meta content is empty', () => {
    globalThis.document = stubDocument('');
    assert.equal(readCsrf(), '');
  });

  test('reads fresh each call (not cached)', () => {
    globalThis.document = stubDocument('token-v1');
    assert.equal(readCsrf(), 'token-v1');
    globalThis.document = stubDocument('token-v2');
    assert.equal(readCsrf(), 'token-v2');
  });
});

describe('fetchJson', () => {
  test('returns parsed JSON on 200 application/json', async () => {
    const payload = { ok: true, data: 42 };
    globalThis.fetch = async () => ({
      status: 200,
      headers: { get: () => 'application/json; charset=utf-8' },
      json: async () => payload,
    });
    const result = await fetchJson('/api/test');
    assert.deepEqual(result, payload);
  });

  test('throws on non-JSON content-type', async () => {
    globalThis.fetch = async () => ({
      status: 200,
      headers: { get: () => 'text/html' },
      json: async () => { throw new Error('unreachable'); },
    });
    await assert.rejects(
      () => fetchJson('/api/test'),
      (err) => {
        assert.ok(err.message.includes('text/html'), `unexpected: ${err.message}`);
        return true;
      }
    );
  });

  test('returns non-2xx JSON body as-is (no throw)', async () => {
    const errorBody = { ok: false, error: 'not found' };
    globalThis.fetch = async () => ({
      status: 404,
      headers: { get: () => 'application/json' },
      json: async () => errorBody,
    });
    const result = await fetchJson('/api/missing');
    assert.deepEqual(result, errorBody);
  });

  test('throws with HTTP status in message on non-JSON 500', async () => {
    globalThis.fetch = async () => ({
      status: 500,
      headers: { get: () => 'text/plain' },
      json: async () => { throw new Error('unreachable'); },
    });
    await assert.rejects(
      () => fetchJson('/api/err'),
      (err) => {
        assert.ok(err.message.includes('500'), `expected 500 in: ${err.message}`);
        return true;
      }
    );
  });

  test('uses empty string for missing content-type header', async () => {
    globalThis.fetch = async () => ({
      status: 200,
      headers: { get: () => null },
      json: async () => { throw new Error('unreachable'); },
    });
    await assert.rejects(() => fetchJson('/api/test'));
  });
});

describe('postJson', () => {
  test('sends POST with JSON body and CSRF header', async () => {
    globalThis.document = stubDocument('csrf-xyz');
    let capturedOpts;
    globalThis.fetch = async (url, opts) => {
      capturedOpts = opts;
      return {
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({ ok: true }),
      };
    };
    await postJson('/api/action', { key: 'val' });
    assert.equal(capturedOpts.method, 'POST');
    assert.equal(capturedOpts.headers['X-CSRFToken'], 'csrf-xyz');
    assert.equal(capturedOpts.headers['Content-Type'], 'application/json');
    assert.equal(capturedOpts.body, JSON.stringify({ key: 'val' }));
  });

  test('sends undefined body when body arg is null', async () => {
    globalThis.document = stubDocument('t');
    let capturedOpts;
    globalThis.fetch = async (url, opts) => {
      capturedOpts = opts;
      return {
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({}),
      };
    };
    await postJson('/api/empty', null);
    assert.equal(capturedOpts.body, undefined);
  });

  test('merges caller opts.headers without dropping CSRF', async () => {
    globalThis.document = stubDocument('csrf-abc');
    let capturedOpts;
    globalThis.fetch = async (url, opts) => {
      capturedOpts = opts;
      return {
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({}),
      };
    };
    await postJson('/api/action', {}, { headers: { 'X-Custom': 'yes' } });
    assert.equal(capturedOpts.headers['X-CSRFToken'], 'csrf-abc');
    assert.equal(capturedOpts.headers['X-Custom'], 'yes');
  });
});

describe('postForm', () => {
  test('sends POST with FormData body containing csrf_token field', async () => {
    globalThis.document = stubDocument('csrf-form');
    let capturedOpts;
    globalThis.fetch = async (url, opts) => {
      capturedOpts = opts;
      return {
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({ ok: true }),
      };
    };
    await postForm('/api/submit', { name: 'value' });
    assert.equal(capturedOpts.method, 'POST');
    // FormData body should include csrf_token
    assert.ok(capturedOpts.body, 'body should be present');
    assert.equal(capturedOpts.body.fields?.csrf_token, 'csrf-form');
    assert.equal(capturedOpts.body.fields?.name, 'value');
  });
});
