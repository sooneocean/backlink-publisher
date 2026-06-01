// Shared config-form profile/editor functions (native ES module).
//
// The 7 functions that index_main.js and settings_main.js both declared
// (toggleEditor/markDirty/saveEdit/cancelEdit/loadProfile/loadBatchProfile/
// saveProfilePrompt). They were DEAD in settings (no settings template calls
// them); the live copies belong to the index config form. Factored here as a
// factory closing over the page's plans/profiles data so index.js (and any
// future config-form page) shares one implementation.
//
// createConfigForm({ plansData, profiles }) → the bound handlers.

import { fetchJson, readCsrf } from './api.js';

export function createConfigForm({ plansData = [], profiles = [] } = {}) {
  function _rebuildPlansJsonl() {
    return plansData.map((p) => JSON.stringify(p)).join('\n');
  }
  function _syncPlansFields() {
    const jsonl = _rebuildPlansJsonl();
    document.querySelectorAll('input[name="plans"]').forEach((el) => { el.value = jsonl; });
  }

  // ── Inline article editor ──
  function toggleEditor(idx) {
    const el = document.getElementById('editor-' + idx);
    const btn = document.getElementById('editBtn-' + idx);
    if (!el || !btn) return;
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
    if (!ta) return;
    if (plansData[idx]) { plansData[idx].content_markdown = ta.value; _syncPlansFields(); }
    const s = document.getElementById('editStatus-' + idx);
    if (s) { s.textContent = '✓ 已保存'; s.style.color = 'var(--success)'; }
    const preview = document.getElementById('preview-' + idx);
    if (preview) preview.innerHTML = '<em style="color:#6b7280;font-size:12px;">内容已修改</em>';
  }
  function cancelEdit(idx, original) {
    const ta = document.getElementById('editorArea-' + idx);
    if (!ta) return;
    // index passes the markdown as a JSON string (Jinja `|tojson`) — parse to plain text.
    const content = JSON.parse(original);
    ta.value = content;
    if (plansData[idx]) { plansData[idx].content_markdown = content; _syncPlansFields(); }
    const s = document.getElementById('editStatus-' + idx);
    if (s) { s.textContent = '已还原'; s.style.color = ''; }
  }

  // ── Campaign profiles ──
  function loadProfile(idx) {
    if (idx === '') return;
    const p = profiles[parseInt(idx, 10)];
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
    const p = profiles[parseInt(idx, 10)];
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
    data.append('csrf_token', readCsrf());
    fetchJson('/profiles/save', { method: 'POST', body: data, headers: { 'X-CSRFToken': readCsrf() } })
      .then((d) => {
        if (d.ok) alert('配置「' + name.trim() + '」已保存 ✓');
        else alert('保存失败：' + (d.error || '未知错误'));
      })
      .catch((e) => alert('保存失败：' + e.message));
  }

  return { toggleEditor, markDirty, saveEdit, cancelEdit, loadProfile, loadBatchProfile, saveProfilePrompt };
}
