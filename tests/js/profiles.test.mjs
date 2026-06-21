// Vitest tests for webui_app/static/js/lib/profiles.js
// Run from webui_app/static/js/ with: npx vitest run
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createConfigForm } from '../../webui_app/static/js/lib/profiles.js';

// createConfigForm closes over plansData/profiles and reads/writes the live DOM
// via getElementById / querySelector. Each test rebuilds the exact node ids the
// source touches so we exercise the real branches, not a mock seam.

describe('lib/profiles.createConfigForm — inline editor', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="editor-0" style="display:none"></div>
      <button id="editBtn-0"></button>
      <span id="editStatus-0"></span>
      <textarea id="editorArea-0"></textarea>
      <div id="preview-0"></div>
      <input name="plans" value="" />
    `;
  });

  it('toggleEditor flips display and button label', () => {
    const plansData = [{ content_markdown: 'a' }];
    const { toggleEditor } = createConfigForm({ plansData });
    const editor = document.getElementById('editor-0');
    const btn = document.getElementById('editBtn-0');

    expect(editor.style.display).toBe('none');
    toggleEditor(0);
    expect(editor.style.display).toBe('block');
    expect(btn.innerHTML).toContain('收起编辑器');
    toggleEditor(0);
    expect(editor.style.display).toBe('none');
    expect(btn.innerHTML).toContain('编辑内容');
  });

  it('toggleEditor is a no-op when target nodes are missing', () => {
    const { toggleEditor } = createConfigForm({ plansData: [] });
    expect(() => toggleEditor(99)).not.toThrow();
  });

  it('markDirty marks the unsaved hint', () => {
    const { markDirty } = createConfigForm({ plansData: [{}] });
    markDirty(0);
    expect(document.getElementById('editStatus-0').textContent).toBe('（未保存）');
  });

  it('saveEdit writes textarea content into plansData and syncs the plans field', () => {
    const plansData = [{ content_markdown: 'old' }];
    const { saveEdit } = createConfigForm({ plansData });
    document.getElementById('editorArea-0').value = 'new body';

    saveEdit(0);

    expect(plansData[0].content_markdown).toBe('new body');
    // _syncPlansFields serialises every plan back into input[name="plans"].
    const field = document.querySelector('input[name="plans"]');
    expect(JSON.parse(field.value)).toEqual({ content_markdown: 'new body' });
    expect(document.getElementById('editStatus-0').textContent).toBe('✓ 已保存');
  });

  it('cancelEdit restores from the JSON-encoded original', () => {
    const plansData = [{ content_markdown: 'edited' }];
    const { cancelEdit } = createConfigForm({ plansData });
    const ta = document.getElementById('editorArea-0');
    ta.value = 'edited';

    // index.js passes the original markdown as a JSON string (Jinja |tojson).
    cancelEdit(0, JSON.stringify('original text'));

    expect(ta.value).toBe('original text');
    expect(plansData[0].content_markdown).toBe('original text');
    expect(document.getElementById('editStatus-0').textContent).toBe('已还原');
  });
});

describe('lib/profiles.createConfigForm — profile loaders', () => {
  it('loadProfile applies profile fields to the configForm selects', () => {
    document.body.innerHTML = `
      <form id="configForm">
        <select name="platform"><option>blogger</option><option>medium</option></select>
        <select name="target_language"><option>zh-CN</option><option>en</option></select>
        <select name="url_mode"><option>A</option><option>B</option></select>
        <select name="publish_mode"><option>draft</option><option>publish</option></select>
      </form>
      <select id="profilePicker"><option value="">-</option><option value="0">p0</option></select>
    `;
    const profiles = [{ platform: 'medium', language: 'en', url_mode: 'B', publish_mode: 'publish' }];
    const { loadProfile } = createConfigForm({ profiles });

    loadProfile('0');

    const form = document.getElementById('configForm');
    expect(form.querySelector('select[name="platform"]').value).toBe('medium');
    expect(form.querySelector('select[name="target_language"]').value).toBe('en');
    expect(form.querySelector('select[name="url_mode"]').value).toBe('B');
    expect(form.querySelector('select[name="publish_mode"]').value).toBe('publish');
    // picker resets to the empty sentinel after a load.
    expect(document.getElementById('profilePicker').value).toBe('');
  });

  it('loadProfile is a no-op for the empty sentinel and out-of-range index', () => {
    document.body.innerHTML = '<form id="configForm"></form>';
    const { loadProfile } = createConfigForm({ profiles: [] });
    expect(() => { loadProfile(''); loadProfile('5'); }).not.toThrow();
  });

  it('loadBatchProfile applies fields to the batchForm selects', () => {
    document.body.innerHTML = `
      <form id="batchForm">
        <select name="platform"><option>blogger</option><option>medium</option></select>
        <select name="language"><option>zh-CN</option><option>en</option></select>
        <select name="url_mode"><option>A</option><option>B</option></select>
        <select name="publish_mode"><option>draft</option><option>publish</option></select>
      </form>
    `;
    const profiles = [{ platform: 'medium', language: 'en', url_mode: 'B', publish_mode: 'publish' }];
    const { loadBatchProfile } = createConfigForm({ profiles });

    loadBatchProfile('0');

    const form = document.getElementById('batchForm');
    expect(form.querySelector('select[name="platform"]').value).toBe('medium');
    expect(form.querySelector('select[name="language"]').value).toBe('en');
    expect(form.querySelector('select[name="url_mode"]').value).toBe('B');
    expect(form.querySelector('select[name="publish_mode"]').value).toBe('publish');
  });
});

describe('lib/profiles.createConfigForm — saveProfilePrompt', () => {
  afterEach(() => vi.restoreAllMocks());

  it('POSTs the named profile with a fresh csrf_token', async () => {
    document.head.innerHTML = '';
    const meta = document.createElement('meta');
    meta.name = 'csrf-token';
    meta.content = 'tok-xyz';
    document.head.appendChild(meta);
    document.body.innerHTML = `
      <form id="configForm">
        <select name="platform"><option>blogger</option><option selected>medium</option></select>
        <select name="target_language"><option selected>zh-CN</option></select>
        <select name="url_mode"><option selected>A</option></select>
        <select name="publish_mode"><option selected>draft</option></select>
      </form>
    `;
    vi.stubGlobal('prompt', vi.fn().mockReturnValue('my-profile'));
    vi.stubGlobal('alert', vi.fn());
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      headers: { get: (k) => (k === 'content-type' ? 'application/json' : '') },
      json: async () => ({ ok: true }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { saveProfilePrompt } = createConfigForm({});
    saveProfilePrompt();
    // let the fetchJson promise chain settle
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/profiles/save');
    expect(opts.method).toBe('POST');
    expect(opts.headers['X-CSRFToken']).toBe('tok-xyz');
    const fd = opts.body;
    expect(fd).toBeInstanceOf(FormData);
    expect(fd.get('profile_name')).toBe('my-profile');
    expect(fd.get('platform')).toBe('medium');
    expect(fd.get('csrf_token')).toBe('tok-xyz');
  });

  it('aborts without fetching when the prompt is cancelled or blank', () => {
    vi.stubGlobal('prompt', vi.fn().mockReturnValue('   '));
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const { saveProfilePrompt } = createConfigForm({});
    saveProfilePrompt();

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
