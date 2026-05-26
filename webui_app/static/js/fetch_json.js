// Shared guarded fetch helper.
//
// Returns the parsed JSON body, or throws a meaningful Error when the server
// responds with a non-JSON body (e.g. a Flask HTML error/abort page). Without
// this guard, `await resp.json()` / `r.json()` throws a cryptic
// `SyntaxError: Unexpected token '<'` that masks the real HTTP failure — the
// operator sees "network error" or nothing at all instead of the root cause.
// See feedback_fetch_json_must_guard_content_type.
//
// Contract: a non-2xx response that IS JSON is returned as-is — callers inspect
// their own {ok | status | error} fields. ONLY a non-JSON body throws, so error
// payloads like {ok:false, error:"..."} still reach the caller unchanged.
(function () {
  window.fetchJson = async function (url, opts) {
    const resp = await fetch(url, opts);
    const ct = resp.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
      const kind = ct.split(';')[0] || '未知类型';
      throw new Error('服务器返回非 JSON 响应 (HTTP ' + resp.status + ' ' + kind + ')');
    }
    return await resp.json();
  };
})();
