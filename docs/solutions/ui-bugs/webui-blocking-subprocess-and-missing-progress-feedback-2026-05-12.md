---
title: WebUI Blocking Subprocess Calls and Missing Progress Feedback
date: 2026-05-12
category: ui-bugs
module: webui
problem_type: ui_bug
component: tooling
severity: high
symptoms:
  - Landing page takes 15+ seconds to load on every request
  - Long-running AI generation (30-60s) freezes the browser with no feedback
  - Submit buttons remain clickable during processing, causing duplicate submissions
  - "发布中心" tab shows permanent error state for a removed dependency
root_cause: missing_tooling
resolution_type: code_fix
tags:
  - blocking-subprocess
  - deprecated-dependency
  - loading-overlay
  - flask-webui
---

# WebUI Blocking Subprocess Calls and Missing Progress Feedback

## Problem

The Flask web UI (`webui.py`) blocked every page load and publish action for up to 15 seconds by calling `subprocess.run(['opencli', 'doctor'], timeout=15)` — a tool that had already been removed from the project. Long-running pipeline operations (AI generation, validation, publish) provided no feedback, making it impossible to tell if the app was working or frozen.

## Symptoms

- `index()` route hangs for 15 seconds on every page load (silent timeout)
- `ce_publish_real()` hangs for another 15 seconds before executing the real publish
- After submitting `/ce:generate` (AI call, 30–60 seconds), browser shows a blank/frozen page with no indication of progress
- Same freeze behavior on `/ce:validate`, `/ce:publish`, `/ce:publish-real`
- Submit buttons remain active during processing — accidental double-submits possible
- "发布中心" tab permanently shows red error state with installation instructions for a non-existent tool

## What Didn't Work

Not applicable — the root cause was dead code (calling a removed tool). No iteration was needed; the fix was surgical removal.

## Solution

### 1. Remove Dead `opencli doctor` Subprocess Calls

**Before** (`index()` route blocked for up to 15 seconds on every visit):

```python
@app.route('/')
def index():
    config = session.get('config', {})

    opencli_status = 'error'
    try:
        result = subprocess.run(['opencli', 'doctor', '--no-live'],
            capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr
        if 'Extension: connected' in output:
            opencli_status = 'connected'
        elif result.returncode == 0:
            opencli_status = 'connected'
    except Exception as e:
        opencli_status = 'error'

    return _render(HTML, config=config, opencli_status=opencli_status, ...)
```

**After** (instant load):

```python
@app.route('/')
def index():
    config = session.get('config', {})
    ready_to_publish = None
    validated = session.get('validated', '')
    if validated:
        ready_to_publish = {'data': validated, 'platform': config.get('platform', 'medium')}
    return _render(HTML, config=config, ready_to_publish=ready_to_publish)
```

Same removal applied to `ce_publish_real()`.

### 2. Add Loading Overlay with Context-Specific Messaging

Inject a full-screen overlay + Bootstrap spinner before `</body>`. Use `document.addEventListener('submit', ...)` to trigger it on any form submit, with route-specific messages so users know what's happening and roughly how long to wait.

```javascript
(function() {
    const MSGS = {
        '/ce:plan':         { text: '分析网址中…',     sub: '正在抓取页面元数据' },
        '/ce:generate':     { text: 'AI 生成文章中…', sub: '调用 AI 生成外链文章，约需 30–60 秒' },
        '/ce:validate':     { text: '验证内容中…',     sub: '检查外链格式与内容合规性' },
        '/ce:publish':      { text: '发布中…',         sub: '正在发布到目标平台，请勿关闭页面' },
        '/ce:publish-real': { text: '正式发布中…',     sub: '正在写入平台，请勿关闭页面' },
    };

    document.addEventListener('submit', function(e) {
        const form = e.target;
        const action = (form.getAttribute('action') || '').split('?')[0];
        if (action === '/ce:clear') return;  // Skip instant actions

        const msg = MSGS[action] || { text: '处理中…', sub: '请稍候' };
        document.getElementById('_loadingText').textContent    = msg.text;
        document.getElementById('_loadingSubtext').textContent = msg.sub;
        document.getElementById('_loadingOverlay').style.display = 'flex';

        form.querySelectorAll('[type="submit"]').forEach(btn => { btn.disabled = true; });
    });
})();
```

```html
<div id="_loadingOverlay" style="display:none;position:fixed;inset:0;background:rgba(15,15,15,0.55);z-index:9999;flex-direction:column;align-items:center;justify-content:center;">
    <div style="background:white;border-radius:20px;padding:40px 48px;text-align:center;max-width:320px;box-shadow:0 24px 64px rgba(0,0,0,0.25);">
        <div class="spinner-border mb-4" style="width:3rem;height:3rem;color:var(--primary);" role="status"></div>
        <div id="_loadingText" style="font-size:1.1rem;font-weight:700;color:#1f2937;margin-bottom:8px;">处理中…</div>
        <div id="_loadingSubtext" style="font-size:0.85rem;color:#6b7280;line-height:1.5;">请稍候</div>
    </div>
</div>
```

### 3. Replace Stale Dependency UI with Conditional Publish Panel

Replace the opencli status card in "发布中心" tab with a panel that only renders when the user has validated content ready to publish:

```html
{% if ready_to_publish %}
<div class="publish-status success">
    <h5><i class="bi bi-check-circle-fill me-2"></i>内容已验证，可以发布</h5>
</div>
<form method="POST" action="/ce:publish-real">
    <input type="hidden" name="validated" value="{{ ready_to_publish.data }}">
    <input type="hidden" name="platform" value="{{ ready_to_publish.platform }}">
    <button type="submit" class="btn btn-success">正式发布</button>
</form>
{% else %}
<div class="publish-status pending">
    <h5>尚无待发布内容</h5>
    <p>请先完成文章生成与验证。</p>
</div>
{% endif %}
```

## Why This Works

**Blocking page loads**: The `opencli` binary was removed from the project (`publish_backlinks.py` explicitly marks `--opencli-profile` as deprecated with "Has no effect (OpenCLI removed)"), but `index()` and `ce_publish_real()` still called `subprocess.run(['opencli', 'doctor'], timeout=15)`. Since the binary doesn't exist, `subprocess.run` raises `FileNotFoundError`, caught silently, after the full 15-second timeout.

**Missing progress feedback**: Flask routes process requests synchronously. A `subprocess.run()` call for AI generation blocks for 30–60 seconds with no way for the browser to know work is happening. The JavaScript overlay fires client-side _immediately_ on form submit (before the network request completes), giving instant visual feedback even though the server is still working.

**Stale UI**: The template rendered `opencli_status` error state unconditionally, creating a permanent "broken" appearance with no resolution path. Replacing with a conditional panel eliminates impossible UI states.

## Prevention

**Audit subprocess calls when removing a dependency**

After removing any CLI tool or external integration, grep the entire codebase for the tool name:
```bash
grep -rn "opencli\|tool-name" --include="*.py"
```
Remove or replace every `subprocess.run()` / `Popen()` call that references the removed binary.

**For any blocking operation >5 seconds, add a loading overlay**

Pattern: JavaScript event listener on form submit, triggered before the network request:
```javascript
document.addEventListener('submit', e => {
    if (shouldShowLoader(e.target.action)) showOverlay();
});
```
Include a time estimate in the overlay message (e.g., "约需 30-60 秒") to calibrate user expectations.

**Prevent double-submit on blocking forms**

Disable all submit buttons when overlay appears:
```javascript
form.querySelectorAll('[type="submit"]').forEach(btn => { btn.disabled = true; });
```

**Keep UI state aligned with backend capabilities**

Use context variables to conditionally render UI sections. Never render an "error state" for a non-existent feature — it creates cognitive dissonance. If a feature is removed, remove all UI that references it.

**Synthetic page load test**

Add a latency check to development workflow:
```bash
time curl -s -o /dev/null http://localhost:8888/
# Expected: < 500ms; Red flag: > 2s
```

## Related Issues

- `docs/brainstorms/2026-05-11-publisher-adapters-rewrite-requirements.md` — The larger publisher adapters rewrite explicitly deferred these webui.py fixes ("属 P2-1 单独的小修复 PR"). This solution doc is the record of that companion fix.
- `docs/plans/2026-05-11-001-feat-publisher-adapters-rewrite-plan.md` — Scope boundary referenced "webui.py missing deps" as out of scope; these fixes resolve that known gap.
