    // ── Blog ID 行管理 ───────────────────────────────────────────
    function addRow() {
        const container = document.getElementById('blogIdRows');
        const row = document.createElement('div');
        row.className = 'blog-id-row';
        row.innerHTML = `
            <input type="text" class="form-control form-control-sm" name="domain[]"
                   placeholder="https://your-site.com">
            <input type="text" class="form-control form-control-sm token-box" name="blog_id[]"
                   placeholder="1234567890123456789">
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeRow(this)">
                <i class="bi bi-trash"></i>
            </button>`;
        container.appendChild(row);
    }

    function removeRow(btn) {
        btn.closest('.blog-id-row').remove();
    }

    // ── 复制 Redirect URI ────────────────────────────────────────
    function copyUri() {
        const val = document.getElementById('callbackUriDisplay').value;
        navigator.clipboard.writeText(val).then(() => {
            const btn = document.getElementById('copyBtn');
            btn.innerHTML = '<i class="bi bi-check2 me-1"></i>已复制';
            btn.classList.replace('btn-warning', 'btn-success');
            setTimeout(() => {
                btn.innerHTML = '<i class="bi bi-clipboard me-1"></i>复制';
                btn.classList.replace('btn-success', 'btn-warning');
            }, 2000);
        });
    }

    // ── Client Secret 显示切换 ──────────────────────────────────
    function toggleSecret() {
        const input = document.getElementById('clientSecretInput');
        const icon  = document.getElementById('secretEye');
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'bi bi-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'bi bi-eye';
        }
    }

    // ── LLM Settings Helpers ─────────────────────────────────────
    function toggleLlmSecret() {
        const input = document.getElementById('llmApiKeyInput');
        const icon  = document.getElementById('llmSecretEye');
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'bi bi-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'bi bi-eye';
        }
    }

    function resetLlmPrompt() {
        event.preventDefault();
        if (confirm('确定要恢复默认系统提示词吗？')) {
            document.getElementById('llmPromptInput').value = '';
        }
    }

    function clearLlmSettings(ev) {
        if (confirm('确定要清除所有 LLM 配置吗？这将还原为系统默认生成模式。')) {
            // hidden marker → server-side reset to defaults (all fields, not just visible inputs)
            document.getElementById('llmFormAction').value = 'clear';
            document.querySelector('form[action="/settings/save-llm-config"]').submit();
        }
    }

    function applyLlmPreset(provider) {
        event.preventDefault();
        const endpointEl = document.getElementById('llmEndpointInput');
        const modelEl = document.getElementById('llmModelInput');
        
        const presets = {
            'openai': { url: 'https://api.openai.com/v1', model: 'gpt-4o' },
            'deepseek': { url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
            'openrouter': { url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini' },
            'groq': { url: 'https://api.groq.com/openai/v1', model: 'llama3-70b-8192' },
            'local': { url: 'http://localhost:11434/v1', model: 'llama3' }
        };
        
        if (presets[provider]) {
            endpointEl.value = presets[provider].url;
            modelEl.value = presets[provider].model;
            document.getElementById('llmTestResult').innerHTML = '';
            document.getElementById('modelSelectBtn').classList.add('d-none');
        }
    }

    function selectLlmModel(model) {
        event.preventDefault();
        document.getElementById('llmModelInput').value = model;
        
        // Auto-save the form to bind the selected model
        const form = document.querySelector('form[action="/settings/save-llm-config"]');
        if (form) {
            form.submit();
        }
    }

    async function testLlmConnection() {
        const resEl = document.getElementById('llmTestResult');
        const btn = event.target.closest('button');
        const endpoint = document.querySelector('input[name="endpoint"]').value;
        const api_key = document.querySelector('input[name="api_key"]').value;
        const model = document.querySelector('input[name="model"]').value;

        resEl.innerHTML = '<span class="text-muted"><i class="bi bi-hourglass-split"></i> 测试中...</span>';
        btn.disabled = true;

        try {
            const formData = new FormData();
            formData.append('endpoint', endpoint);
            formData.append('api_key', api_key);
            formData.append('model', model);
            const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
            formData.append('csrf_token', csrf);

            const resp = await fetch('/settings/test-llm-connection', {
                method: 'POST',
                body: formData,
                headers: {'X-CSRFToken': csrf}
            });
            const data = await resp.json();
            if (data.status === 'ok') {
                resEl.innerHTML = `<span class="text-success"><i class="bi bi-check-circle"></i> ${data.message}</span>`;
                
                if (data.models && data.models.length > 0) {
                    const list = document.getElementById('modelDropdownList');
                    const btn = document.getElementById('modelSelectBtn');
                    list.innerHTML = '';
                    data.models.sort().forEach(m => {
                        const li = document.createElement('li');
                        li.innerHTML = `<a class="dropdown-item" href="#" style="font-size:13px;" onclick="selectLlmModel('${m}')">${m}</a>`;
                        list.appendChild(li);
                    });
                    btn.classList.remove('d-none');
                }
            } else {
                resEl.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle"></i> ${data.message}</span>`;
            }
        } catch (e) {
            resEl.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle"></i> 网络错误: ${e}</span>`;
        } finally {
            btn.disabled = false;
        }
    }

    async function previewGeneration() {
        const resEl = document.getElementById('previewResult');
        const title = document.getElementById('testTitle').value;
        resEl.textContent = "生成中...";

        try {
            const formData = new FormData();
            formData.append('test_title', title);
            const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
            formData.append('csrf_token', csrf);
            const resp = await fetch('/settings/test-llm-generation', {
                method: 'POST',
                body: formData,
                headers: {'X-CSRFToken': csrf}
            });
            const data = await resp.json();
            if (data.status === 'ok') {
                resEl.textContent = data.result;
            } else {
                resEl.textContent = "错误: " + data.message;
            }
        } catch (e) {
            resEl.textContent = "请求失败: " + e;
        }
    }

    // ── Medium Token 显示切换 ────────────────────────────────────
    function toggleToken() {
        const input = document.getElementById('mediumTokenInput');
        const icon = document.getElementById('eyeIcon');
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'bi bi-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'bi bi-eye';
        }
    }

    // ── Inline Article Editor ────────────────────────────────────
    const _plansData = (window.__settingsBootstrap && window.__settingsBootstrap.plans_list) || [];

    function _rebuildPlansJsonl() {
        return _plansData.map(p => JSON.stringify(p)).join('\n');
    }

    function _syncPlansFields() {
        const jsonl = _rebuildPlansJsonl();
        document.querySelectorAll('input[name="plans"]').forEach(el => { el.value = jsonl; });
    }

    function toggleEditor(idx) {
        const el = document.getElementById('editor-' + idx);
        const btn = document.getElementById('editBtn-' + idx);
        if (el.style.display === 'none') {
            el.style.display = 'block';
            btn.innerHTML = '<i class="bi bi-eye me-1"></i>收起编辑器';
        } else {
            el.style.display = 'none';
            btn.innerHTML = '<i class="bi bi-pencil me-1"></i>编辑内容';
        }
    }

    function markDirty(idx) {
        const status = document.getElementById('editStatus-' + idx);
        if (status) status.textContent = '（未保存）';
    }

    function saveEdit(idx) {
        const ta = document.getElementById('editorArea-' + idx);
        const newContent = ta.value;
        if (_plansData[idx]) {
            _plansData[idx].content_markdown = newContent;
            _syncPlansFields();
        }
        const status = document.getElementById('editStatus-' + idx);
        if (status) { status.textContent = '✓ 已保存'; status.style.color = 'var(--success)'; }
        const preview = document.getElementById('preview-' + idx);
        if (preview) preview.innerHTML = '<em style="color:#6b7280;font-size:12px;">内容已修改，请展开预览查看</em>';
    }

    function cancelEdit(idx, original) {
        const ta = document.getElementById('editorArea-' + idx);
        ta.value = original;
        if (_plansData[idx]) {
            _plansData[idx].content_markdown = original;
            _syncPlansFields();
        }
        const status = document.getElementById('editStatus-' + idx);
        if (status) { status.textContent = '已还原'; status.style.color = ''; }
    }

    // ── Campaign Profiles ────────────────────────────────────────
    const _PROFILES = (window.__settingsBootstrap && window.__settingsBootstrap.profiles) || [];

    function loadProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const form = document.getElementById('configForm');
        const setSelect = (name, val) => {
            const el = form ? form.querySelector('select[name="' + name + '"]')
                            : document.querySelector('select[name="' + name + '"]');
            if (el) el.value = val;
        };
        setSelect('platform', p.platform || 'blogger');
        setSelect('target_language', p.language || 'zh-CN');
        setSelect('url_mode', p.url_mode || 'A');
        setSelect('publish_mode', p.publish_mode || 'draft');
        const picker = document.getElementById('profilePicker');
        if (picker) picker.value = '';
    }

    function loadBatchProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const setSelect = (id, val) => {
            const el = document.querySelector('#batchForm select[name="' + id + '"]');
            if (el) el.value = val;
        };
        setSelect('platform', p.platform || 'blogger');
        setSelect('language', p.language || 'zh-CN');
        setSelect('url_mode', p.url_mode || 'A');
        setSelect('publish_mode', p.publish_mode || 'draft');
    }

    function saveProfilePrompt() {
        const name = prompt('配置名称（如：51acgs-zh-blogger）：', '');
        if (!name || !name.trim()) return;
        const getVal = (sel) => {
            const el = document.querySelector('select[name="' + sel + '"]');
            return el ? el.value : '';
        };
        const data = new FormData();
        data.append('profile_name', name.trim());
        data.append('platform', getVal('platform'));
        data.append('language', getVal('target_language'));
        data.append('url_mode', getVal('url_mode'));
        data.append('publish_mode', getVal('publish_mode'));
        const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
        data.append('csrf_token', csrf);
        fetch('/profiles/save', { method: 'POST', body: data, headers: {'X-CSRFToken': csrf} })
            .then(r => r.json())
            .then(d => {
                if (d.ok) alert('配置「' + name.trim() + '」已保存 ✓');
                else alert('保存失败：' + (d.error || '未知错误'));
            });
    }

    // ── deep-link → 自动展开所在折叠面板 ───────────────────────
    function _openCollapseForHash() {
        const id = window.location.hash.slice(1);
        if (!id) return;
        const target = document.getElementById(id);
        if (!target) return;
        const panel = target.closest('.collapse');
        if (panel && !panel.classList.contains('show')) {
            bootstrap.Collapse.getOrCreateInstance(panel).show();
            panel.addEventListener('shown.bs.collapse',
                () => target.scrollIntoView({block: 'start'}), {once: true});
        }
    }
    document.addEventListener('DOMContentLoaded', _openCollapseForHash);
    window.addEventListener('hashchange', _openCollapseForHash);

    // ── Bind channel: sessionStorage reopen (Plan 2026-05-19-001 Unit 5) ──
    // After a bind click + page reload, restore the open card so the operator
    // sees the badge transition in context. The key is cleared after reopen.
    document.addEventListener('DOMContentLoaded', function() {
        var last = null;
        try { last = sessionStorage.getItem('bind:lastChannel'); } catch (e) {}
        if (!last) return;
        var card = document.getElementById('channel-' + last);
        if (card && !card.classList.contains('show')) {
            try { bootstrap.Collapse.getOrCreateInstance(card).show(); } catch (e) {}
            card.addEventListener('shown.bs.collapse', function() {
                var section = document.getElementById('bind-section-' + last);
                if (section) section.scrollIntoView({block: 'center'});
            }, {once: true});
        }
        try { sessionStorage.removeItem('bind:lastChannel'); } catch (e) {}
    });

    // ── Loading Overlay ──────────────────────────────────────────
    (function() {
        const MSGS = {
            '/ce:plan':         { text: '分析网址中…',     sub: '正在抓取页面元数据' },
            '/ce:generate':     { text: 'AI 生成文章中…', sub: '调用 AI 生成外链文章，约需 30–60 秒' },
            '/ce:validate':     { text: '验证内容中…',     sub: '检查外链格式与内容合规性' },
            '/ce:publish':      { text: '发布中…',         sub: '正在发布到目标平台，请勿关闭页面' },
            '/ce:publish-real': { text: '正式发布中…',     sub: '正在写入平台，请勿关闭页面' },
            '/ce:batch':        { text: '批量发布中…',     sub: '正在逐篇生成并发布，每篇约 30–60 秒，请勿关闭页面' },
            '/settings/medium/launch-browser-login': {
                text: '正在打开浏览器…',
                sub: '请在弹出窗口中完成登录（含可能的 email 验证码 / 2FA），完成后页面自动跳转',
            },
            '/settings/medium/probe-browser-login': {
                text: '探测 Medium 登录状态…',
                sub: '约需 5–15 秒',
            },
        };

        document.addEventListener('submit', function(e) {
            const form = e.target;
            const action = (form.getAttribute('action') || '').split('?')[0];
            if (['/ce:clear','/ce:history/delete','/ce:history/update-status'].includes(action)) return;

            const msg = MSGS[action] || { text: '处理中…', sub: '请稍候' };
            document.getElementById('_loadingText').textContent    = msg.text;
            document.getElementById('_loadingSubtext').textContent = msg.sub;
            document.getElementById('_loadingOverlay').style.display = 'flex';

            form.querySelectorAll('[type="submit"]').forEach(function(btn) {
                btn.disabled = true;
            });
        });
    })();

    // ── AI Banner Image Gen Test Connection ─────────────────────
    async function testImageGenConnection() {
        const btn = document.getElementById('testImageGenBtn');
        const result = document.getElementById('imageGenTestResult');
        if (!btn || !result) return;

        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Testing…';
        result.style.display = 'block';
        result.innerHTML = '<div class="alert alert-info mb-0" style="font-size:13px;">Probing <code>&lt;base_url&gt;/models</code>…</div>';

        try {
            const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
            const resp = await fetch('/settings/test-image-gen', {method: 'POST', headers: {'X-CSRFToken': csrf}});
            const data = await resp.json();
            if (data.ok) {
                const modelLine = (data.model_count !== undefined)
                    ? `endpoint reachable; ${data.model_count} models advertised`
                    : 'endpoint reachable';
                result.innerHTML = `<div class="alert alert-success mb-0" style="font-size:13px;">
                    <i class="bi bi-check-circle me-1"></i>${modelLine}
                    ${data.configured_model ? `<br><small>Configured model: <code>${data.configured_model}</code></small>` : ''}
                </div>`;
            } else {
                result.innerHTML = `<div class="alert alert-danger mb-0" style="font-size:13px;">
                    <i class="bi bi-x-circle me-1"></i>${data.error || 'unknown error'}
                </div>`;
            }
        } catch (e) {
            result.innerHTML = `<div class="alert alert-danger mb-0" style="font-size:13px;">
                <i class="bi bi-x-circle me-1"></i>fetch failed: ${e.message}
            </div>`;
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-cloud-check me-1"></i>Test Connection';
        }
    }
