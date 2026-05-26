    function loadHistory(id) {
        window.location.href = '/ce:history?id=' + id;
    }

    // ── Inline Article Editor ────────────────────────────────────
    const _plansData = (window.__indexBootstrap && window.__indexBootstrap.plans_list) || [];

    function _rebuildPlansJsonl() {
        return _plansData.map(p => JSON.stringify(p)).join('\\n');
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
        const s = document.getElementById('editStatus-' + idx);
        if (s) s.textContent = '（未保存）';
    }
    function saveEdit(idx) {
        const ta = document.getElementById('editorArea-' + idx);
        if (_plansData[idx]) { _plansData[idx].content_markdown = ta.value; _syncPlansFields(); }
        const s = document.getElementById('editStatus-' + idx);
        if (s) { s.textContent = '✓ 已保存'; s.style.color = 'var(--success)'; }
        const preview = document.getElementById('preview-' + idx);
        if (preview) preview.innerHTML = '<em style="color:#6b7280;font-size:12px;">内容已修改</em>';
    }
    function cancelEdit(idx, original) {
        const ta = document.getElementById('editorArea-' + idx);
        ta.value = original;
        if (_plansData[idx]) { _plansData[idx].content_markdown = original; _syncPlansFields(); }
        const s = document.getElementById('editStatus-' + idx);
        if (s) { s.textContent = '已还原'; s.style.color = ''; }
    }

    // ── Campaign Profiles ────────────────────────────────────────
    const _PROFILES = (window.__indexBootstrap && window.__indexBootstrap.profiles) || [];

    function loadProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const form = document.getElementById('configForm');
        const setVal = (name, val) => {
            const el = form ? form.querySelector('select[name="' + name + '"]')
                            : document.querySelector('select[name="' + name + '"]');
            if (el) el.value = val;
        };
        setVal('platform', p.platform || 'blogger');
        setVal('target_language', p.language || 'zh-CN');
        setVal('url_mode', p.url_mode || 'A');
        setVal('publish_mode', p.publish_mode || 'draft');
        const picker = document.getElementById('profilePicker');
        if (picker) picker.value = '';
    }
    function loadBatchProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const setVal = (name, val) => {
            const el = document.querySelector('#batchForm select[name="' + name + '"]');
            if (el) el.value = val;
        };
        setVal('platform', p.platform || 'blogger');
        setVal('language', p.language || 'zh-CN');
        setVal('url_mode', p.url_mode || 'A');
        setVal('publish_mode', p.publish_mode || 'draft');
    }
    function saveProfilePrompt() {
        const name = prompt('配置名称（如：51acgs-zh-blogger）：', '');
        if (!name || !name.trim()) return;
        const form = document.getElementById('configForm');
        const getVal = (sel) => {
            const el = form ? form.querySelector('select[name="' + sel + '"]')
                           : document.querySelector('select[name="' + sel + '"]');
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
        fetchJson('/profiles/save', { method: 'POST', body: data, headers: {'X-CSRFToken': csrf} })
            .then(d => {
                if (d.ok) alert('配置「' + name.trim() + '」已保存 ✓');
                else alert('保存失败：' + (d.error || '未知错误'));
            })
            .catch(e => alert('保存失败：' + e.message));
    }

    // ── Loading Overlay ──────────────────────────────────────────
    (function() {
        const MSGS = {
            '/ce:plan':              { text: '分析网址中…',     sub: '正在抓取页面元数据' },
            '/ce:generate':          { text: 'AI 生成文章中…', sub: '调用 AI 生成外链文章，约需 30–60 秒' },
            '/ce:validate':          { text: '验证内容中…',     sub: '检查外链格式与内容合规性' },
            '/ce:publish':           { text: '发布中…',         sub: '正在发布到目标平台，请勿关闭页面' },
            '/ce:publish-real':      { text: '正式发布中…',     sub: '正在写入平台，请勿关闭页面' },
            '/ce:batch':             { text: '批量发布中…',     sub: '正在逐篇生成并发布，每篇约 30–60 秒，请勿关闭页面' },
            '/checkpoint/resume':    { text: '恢复发布中…',     sub: '正在处理未完成的发布任务，可能需要数分钟，请勿关闭页面' },
        };
        document.addEventListener('submit', function(e) {
            const form = e.target;
            const action = (form.getAttribute('action') || '').split('?')[0];
            if (['/ce:clear','/ce:history/delete','/ce:history/update-status'].includes(action)) return;
            const msg = MSGS[action] || { text: '处理中…', sub: '请稍候' };
            document.getElementById('_loadingText').textContent    = msg.text;
            document.getElementById('_loadingSubtext').textContent = msg.sub;
            document.getElementById('_loadingOverlay').style.display = 'flex';
            form.querySelectorAll('[type="submit"]').forEach(function(btn) { btn.disabled = true; });
        });
    })();

    // ── 发布历史筛选器（状态 × 平台，前端 AND，刷新即重置） ──
    (function initHistoryFilter() {
        var cardBody = document.getElementById('historyCardBody');
        if (!cardBody) return;
        var items = cardBody.querySelectorAll('.history-item[data-status]');
        if (!items.length) return;

        var chips = cardBody.querySelectorAll('.filter-chip');
        var emptyFiltered = document.getElementById('historyEmptyFiltered');
        var currentStatus = 'all';
        var currentPlatform = 'all';

        function applyFilter() {
            var visible = 0;
            items.forEach(function(item) {
                var matchStatus = (currentStatus === 'all') || (item.dataset.status === currentStatus);
                var matchPlatform = (currentPlatform === 'all') || (item.dataset.platform === currentPlatform);
                if (matchStatus && matchPlatform) {
                    item.style.display = '';
                    visible++;
                } else {
                    item.style.display = 'none';
                }
            });
            if (emptyFiltered) {
                emptyFiltered.style.display = visible === 0 ? '' : 'none';
            }
        }

        function initCounts() {
            // tally counts per (group, value); 'all' = total
            // Platform keys are server-rendered from the publisher registry
            // (Plan 2026-05-19-002 U2 / R7c) so a new register("X", ...)
            // automatically gets a counter slot without a template edit.
            var counts = { status: { all: 0, drafted: 0, published: 0, failed: 0, other: 0 },
                           platform: Object.assign({all: 0}, Object.fromEntries((window.__indexBootstrap && window.__indexBootstrap.platform_slugs || []).map(function(s) { return [s, 0]; })), {other: 0}) };
            items.forEach(function(item) {
                counts.status.all++;
                counts.platform.all++;
                var st = item.dataset.status;
                var pf = item.dataset.platform;
                if (counts.status[st] !== undefined) counts.status[st]++;
                if (counts.platform[pf] !== undefined) counts.platform[pf]++;
            });
            // Plan 2026-05-19-006: 'unverified' chip count (not in initial set)
            var unverifiedCount = 0;
            items.forEach(function(item) {
                if (item.dataset.status === 'unverified') unverifiedCount++;
            });
            counts.status.unverified = unverifiedCount;
            chips.forEach(function(chip) {
                var group = chip.dataset.filterGroup;
                var value = chip.dataset.filterValue;
                var span = chip.querySelector('.chip-count');
                if (span && counts[group] && counts[group][value] !== undefined) {
                    span.textContent = counts[group][value];
                }
            });
        }

        chips.forEach(function(chip) {
            chip.addEventListener('click', function() {
                var group = chip.dataset.filterGroup;
                var value = chip.dataset.filterValue;
                if (group === 'status') currentStatus = value;
                else if (group === 'platform') currentPlatform = value;
                cardBody.querySelectorAll('.filter-chip[data-filter-group="' + group + '"]').forEach(function(sibling) {
                    sibling.classList.remove('active');
                });
                chip.classList.add('active');
                applyFilter();
                if (typeof window.__rewireBulkSelect === 'function') window.__rewireBulkSelect();
            });
        });

        initCounts();
        applyFilter();
    })();

    // ── Plan 2026-05-19-006 Unit 6: bulk-select select-all / button enable ──
    (function initBulkSelect() {
        function wireSection(rootId, selectAllId, checkboxClass, countLabelId, btnClass) {
            var root = document.getElementById(rootId);
            if (!root) return null;
            var selectAll = document.getElementById(selectAllId);
            var countLabel = document.getElementById(countLabelId);
            if (!selectAll) return null;
            var buttons = document.querySelectorAll('.' + btnClass);

            function visibleCheckboxes() {
                return Array.prototype.filter.call(
                    root.querySelectorAll('.' + checkboxClass),
                    function(cb) {
                        // Excluded if the host history-item is hidden by chip filter
                        var host = cb.closest('.history-item');
                        return host && host.style.display !== 'none';
                    }
                );
            }
            function refresh() {
                var visible = visibleCheckboxes();
                var checked = visible.filter(function(cb) { return cb.checked; });
                if (countLabel) {
                    countLabel.textContent = '(' + checked.length + '/' + visible.length + ')';
                }
                buttons.forEach(function(btn) {
                    btn.disabled = checked.length === 0;
                });
                if (visible.length === 0) {
                    selectAll.indeterminate = false;
                    selectAll.checked = false;
                } else if (checked.length === visible.length) {
                    selectAll.indeterminate = false;
                    selectAll.checked = true;
                } else if (checked.length === 0) {
                    selectAll.indeterminate = false;
                    selectAll.checked = false;
                } else {
                    selectAll.indeterminate = true;
                }
            }
            selectAll.addEventListener('change', function() {
                var target = selectAll.checked;
                visibleCheckboxes().forEach(function(cb) { cb.checked = target; });
                refresh();
            });
            document.addEventListener('change', function(e) {
                if (e.target.classList && e.target.classList.contains(checkboxClass)) refresh();
            });
            return refresh;
        }
        var refreshDraft = wireSection(
            'draftCardBody', 'draftSelectAll', 'draft-bulk-select',
            'draftSelectedCount', 'draft-bulk-btn'
        );
        var refreshHistory = wireSection(
            'historyCardBody', 'historySelectAll', 'history-bulk-select',
            'historySelectedCount', 'history-bulk-btn'
        );
        // Re-evaluate after chip filter toggles visibility — checkboxes on
        // now-hidden items must be unchecked so the bulk POST doesn't grab them.
        window.__rewireBulkSelect = function() {
            if (refreshHistory) {
                var root = document.getElementById('historyCardBody');
                if (root) {
                    root.querySelectorAll('.history-bulk-select').forEach(function(cb) {
                        var host = cb.closest('.history-item');
                        if (host && host.style.display === 'none' && cb.checked) {
                            cb.checked = false;
                        }
                    });
                }
                refreshHistory();
            }
            if (refreshDraft) refreshDraft();
        };
        if (refreshDraft) refreshDraft();
        if (refreshHistory) refreshHistory();

        // Bootstrap tooltips + lazy loaded images fallback handling
        document.addEventListener("DOMContentLoaded", function () {
            var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
            tooltipTriggerList.map(function (tooltipTriggerEl) {
                return new bootstrap.Tooltip(tooltipTriggerEl);
            });

            // Fallback for broken/slow cover images
            document.querySelectorAll(".content-preview img").forEach(function(img) {
                img.onerror = function() {
                    img.style.display = "none";
                    var warn = document.createElement("div");
                    warn.className = "alert alert-secondary py-1 px-2 my-2 d-inline-block";
                    warn.style.fontSize = "12px";
                    warn.innerHTML = "<i class='bi bi-image-alt me-1'></i>封面图片加载失败";
                    img.parentNode.insertBefore(warn, img.nextSibling);
                };
            });
        });
    })();
