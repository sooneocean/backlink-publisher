// Shared API layer (native ES module).
//
// Promotes the former window.fetchJson (fetch_json.js) plus the CSRF-token read
// that was hand-rolled across settings_main.js / index_main.js / channel-binding.js.
// Page modules `import { fetchJson, readCsrf, postForm, postJson } from './lib/api.js'`.

// readCsrf() reads <meta name="csrf-token"> at CALL TIME — never cached into a
// module-level const. Caching would send a stale token if the server rotates it
// mid-session (silent 403 on the fetch transport only). The server's
// _check_csrf_or_abort accepts EITHER a form field `csrf_token` OR an
// `X-CSRFToken` header — this module preserves both transports, narrowing neither.
export function readCsrf() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return (meta && meta.content) || '';
}

// Guarded fetch: returns parsed JSON, or throws a meaningful Error when the
// server responds with a non-JSON body (e.g. a Flask HTML abort page). A non-2xx
// response that IS JSON is returned as-is so {ok:false, error:"..."} payloads
// still reach the caller unchanged. (Ported verbatim from fetch_json.js.)
export async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  const ct = resp.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    const kind = ct.split(';')[0] || '未知类型';
    throw new Error('服务器返回非 JSON 响应 (HTTP ' + resp.status + ' ' + kind + ')');
  }
  return await resp.json();
}

// fetch-transport helper: sends the CSRF token via the X-CSRFToken header (the
// transport the JS flows already used). Reads the token fresh per call.
export async function postJson(url, body, opts = {}) {
  // Destructure to avoid ...opts overwriting the CSRF-injected headers object.
  const { headers: extraHeaders, ...rest } = opts;
  return fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': readCsrf(), ...(extraHeaders || {}) },
    body: body == null ? undefined : JSON.stringify(body),
    ...rest,
  });
}

// form-transport helper: sends the CSRF token via the `csrf_token` form field
// (the transport the form POSTs used). Reads the token fresh per call.
export async function postForm(url, fields = {}, opts = {}) {
  const data = new FormData();
  data.append('csrf_token', readCsrf());
  for (const [k, v] of Object.entries(fields)) data.append(k, v);
  return fetchJson(url, { method: 'POST', body: data, ...opts });
}
