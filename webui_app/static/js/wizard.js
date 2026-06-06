import { fetchJson, postJson, readCsrf } from './lib/api.js';
import { qs, delegate, esc } from './lib/dom.js';

const TOTAL_STEPS = 6;
const platforms = window.__wizardData || [];
let currentStep = 1;
let wizardState = {
    sitemap_urls: [],
    manual_targets: '',
    bookmark_url: '',
    channels: [],
    polling_interval_seconds: 21600,
    default_daily_cap: 10,
    max_daily_publish: 50,
    language_filter: [],
};

function renderStep(step) {
    const content = qs('#wizard-content');
    const prevBtn = qs('#wizard-prev');
    const nextBtn = qs('#wizard-next');
    const launchBtn = qs('#wizard-launch');
    const skipBtn = qs('#wizard-skip');

    prevBtn.style.visibility = step > 1 ? 'visible' : 'hidden';
    nextBtn.classList.toggle('d-none', step === TOTAL_STEPS);
    launchBtn.classList.toggle('d-none', step !== TOTAL_STEPS);
    skipBtn.classList.toggle('d-none', step === TOTAL_STEPS);

    updateStepIndicators(step);

    switch (step) {
        case 1:
            content.innerHTML = renderWelcome();
            break;
        case 2:
            content.innerHTML = renderSeedSources();
            break;
        case 3:
            content.innerHTML = renderChannels();
            break;
        case 4:
            content.innerHTML = renderRules();
            break;
        case 5:
            content.innerHTML = renderReview();
            break;
        case 6:
            content.innerHTML = renderLaunch();
            break;
    }
}

function updateStepIndicators(active) {
    document.querySelectorAll('.wizard-step-indicator').forEach(el => {
        const step = parseInt(el.dataset.step, 10);
        const circle = el.querySelector('.step-circle');
        const label = el.querySelector('.step-label');
        if (step <= active) {
            circle.style.background = '#0d6efd';
            circle.style.color = '#fff';
            label.style.color = '#0d6efd';
            label.style.fontWeight = '500';
        } else {
            circle.style.background = '#dee2e6';
            circle.style.color = '#6c757d';
            label.style.color = '#6c757d';
            label.style.fontWeight = 'normal';
        }
    });
}

function renderWelcome() {
    return `
        <div class="text-center py-4">
            <i class="bi bi-magic" style="font-size:3rem;color:#0d6efd;"></i>
            <h3 class="mt-3">Welcome to Backlink Publisher</h3>
            <p class="text-muted mt-2" style="max-width:500px;margin:0 auto;">
                This wizard will help you set up your automated backlink publishing system in a few steps.
                You will configure seed sources (where to find target URLs), bind your publishing channels,
                and set automation rules.
            </p>
            <div class="mt-4 text-start" style="max-width:400px;margin:0 auto;">
                <div class="d-flex align-items-center mb-2">
                    <i class="bi bi-1-circle text-primary me-2"></i>
                    <span>Add seed sources (sitemaps, URLs, bookmarks)</span>
                </div>
                <div class="d-flex align-items-center mb-2">
                    <i class="bi bi-2-circle text-primary me-2"></i>
                    <span>Bind your publishing channels</span>
                </div>
                <div class="d-flex align-items-center mb-2">
                    <i class="bi bi-3-circle text-primary me-2"></i>
                    <span>Set automation rules and limits</span>
                </div>
                <div class="d-flex align-items-center mb-2">
                    <i class="bi bi-4-circle text-primary me-2"></i>
                    <span>Review and launch</span>
                </div>
            </div>
        </div>
    `;
}

function renderSeedSources() {
    const sm = wizardState.sitemap_urls || [];
    const manual = wizardState.manual_targets || '';
    const bk = wizardState.bookmark_url || '';
    return `
        <h4><i class="bi bi-rss me-2"></i>Seed Sources</h4>
        <p class="text-muted">Add the sources where the system will discover new target URLs.</p>

        <div class="mb-3">
            <label class="form-label fw-semibold">Sitemap URLs</label>
            <div id="sitemap-list">
                ${sm.map((u, i) => `
                    <div class="input-group mb-2">
                        <input type="url" class="form-control form-control-sm sitemap-input" value="${esc(u)}" placeholder="https://example.com/sitemap.xml" data-index="${i}">
                        <button class="btn btn-outline-danger btn-sm remove-sitemap" type="button" data-index="${i}">
                            <i class="bi bi-x"></i>
                        </button>
                    </div>
                `).join('')}
                ${sm.length === 0 ? '<p class="text-muted small">No sitemaps added yet.</p>' : ''}
            </div>
            <button type="button" class="btn btn-outline-primary btn-sm mt-1" id="add-sitemap-btn">
                <i class="bi bi-plus me-1"></i>Add sitemap URL
            </button>
        </div>

        <div class="mb-3">
            <label class="form-label fw-semibold">Manual Target URLs</label>
            <textarea class="form-control" id="manual-targets" rows="4" placeholder="https://example.com/target1&#10;https://example.com/target2">${esc(manual)}</textarea>
            <div class="form-text">One URL per line. Lines starting with # are ignored.</div>
        </div>

        <div class="mb-3">
            <label class="form-label fw-semibold">Bookmark File (optional)</label>
            <input type="text" class="form-control" id="bookmark-url" value="${esc(bk)}" placeholder="file:///path/to/bookmarks.html or paste bookmark content">
            <div class="form-text">HTML bookmark file exported from your browser.</div>
        </div>
    `;
}

function renderChannels() {
    const selected = wizardState.channels || [];
    const selectedNames = new Set(selected.map(c => c.channel));
    return `
        <h4><i class="bi bi-share me-2"></i>Publishing Channels</h4>
        <p class="text-muted">Select which channels to use for automated publishing. You can bind channels later in Settings.</p>
        <div class="list-group">
            ${platforms.map(p => {
                const isSelected = selectedNames.has(p.name);
                const existing = selected.find(c => c.channel === p.name);
                return `
                    <div class="list-group-item list-group-item-action d-flex align-items-center ${isSelected ? 'active' : ''}" data-channel="${p.name}" data-action="toggle-channel" style="cursor:pointer;">
                        <div class="form-check me-3">
                            <input class="form-check-input channel-checkbox" type="checkbox" ${isSelected ? 'checked' : ''} data-channel="${p.name}">
                        </div>
                        <div class="flex-grow-1">
                            <strong>${esc(p.name)}</strong>
                        </div>
                        ${isSelected ? `
                        <div class="channel-options" style="font-size:0.85rem;">
                            <label class="small me-2">Daily cap:
                                <input type="number" class="form-control form-control-sm d-inline" style="width:70px;" value="${existing ? existing.daily_cap : 10}" min="1" max="100" data-channel="${p.name}" data-field="daily_cap">
                            </label>
                        </div>
                        ` : ''}
                    </div>
                `;
            }).join('')}
        </div>
        <p class="text-muted small mt-2">
            <i class="bi bi-info-circle me-1"></i>
            Channels must be bound (authenticated) before publishing. Visit Settings → Channels after the wizard to bind unbound channels.
        </p>
    `;
}

function renderRules() {
    const r = wizardState;
    return `
        <h4><i class="bi bi-sliders me-2"></i>Automation Rules</h4>
        <p class="text-muted">Configure how the automation engine behaves.</p>

        <div class="mb-3">
            <label class="form-label fw-semibold">Polling Interval</label>
            <select class="form-select" id="polling-interval">
                <option value="3600" ${r.polling_interval_seconds === 3600 ? 'selected' : ''}>Every hour</option>
                <option value="21600" ${r.polling_interval_seconds === 21600 ? 'selected' : ''}>Every 6 hours (recommended)</option>
                <option value="43200" ${r.polling_interval_seconds === 43200 ? 'selected' : ''}>Every 12 hours</option>
                <option value="86400" ${r.polling_interval_seconds === 86400 ? 'selected' : ''}>Every 24 hours</option>
            </select>
            <div class="form-text">How often to scan seed sources for new URLs.</div>
        </div>

        <div class="row">
            <div class="col-md-6 mb-3">
                <label class="form-label fw-semibold">Default Daily Cap</label>
                <input type="number" class="form-control" id="default-daily-cap" value="${r.default_daily_cap}" min="1" max="100">
                <div class="form-text">Max publishes per channel per day.</div>
            </div>
            <div class="col-md-6 mb-3">
                <label class="form-label fw-semibold">Max Total Daily</label>
                <input type="number" class="form-control" id="max-daily-publish" value="${r.max_daily_publish}" min="1" max="500">
                <div class="form-text">Total publishes across all channels per day.</div>
            </div>
        </div>

        <div class="mb-3">
            <label class="form-label fw-semibold">Language Filter</label>
            <input type="text" class="form-control" id="language-filter" value="${(r.language_filter || []).join(', ')}" placeholder="e.g. en, zh-CN, ko">
            <div class="form-text">Comma-separated language codes. Leave empty for all languages.</div>
        </div>
    `;
}

function renderReview() {
    const sources = wizardState.sitemap_urls || [];
    const manual = wizardState.manual_targets || '';
    const manualCount = manual.split('\n').filter(l => l.trim() && !l.startsWith('#')).length;
    const channels = wizardState.channels || [];
    const rules = wizardState;

    return `
        <h4><i class="bi bi-check2-square me-2"></i>Review Configuration</h4>
        <p class="text-muted">Please review your settings before launching.</p>

        <div class="card mb-3">
            <div class="card-header fw-semibold">Seed Sources</div>
            <div class="card-body">
                ${sources.length > 0 ? `
                <p><strong>Sitemaps:</strong> ${sources.length} URL(s)</p>
                <ul class="small">${sources.map(u => `<li>${esc(u)}</li>`).join('')}</ul>
                ` : '<p class="text-muted">No sitemaps configured.</p>'}
                <p><strong>Manual targets:</strong> ${manualCount} URL(s)</p>
                ${wizardState.bookmark_url ? `<p><strong>Bookmark file:</strong> ${esc(wizardState.bookmark_url)}</p>` : ''}
            </div>
        </div>

        <div class="card mb-3">
            <div class="card-header fw-semibold">Channels</div>
            <div class="card-body">
                ${channels.length > 0 ? `
                <ul class="list-unstyled">
                    ${channels.map(c => `<li><i class="bi bi-check-circle text-success me-1"></i> <strong>${esc(c.channel)}</strong> — daily cap: ${c.daily_cap}</li>`).join('')}
                </ul>
                ` : '<p class="text-muted">No channels selected. You can configure them later in Settings.</p>'}
            </div>
        </div>

        <div class="card mb-3">
            <div class="card-header fw-semibold">Automation Rules</div>
            <div class="card-body">
                <p><strong>Polling interval:</strong> every ${rules.polling_interval_seconds / 3600} hour(s)</p>
                <p><strong>Daily cap:</strong> ${rules.default_daily_cap} per channel (max ${rules.max_daily_publish} total)</p>
                ${rules.language_filter && rules.language_filter.length > 0 ? `<p><strong>Language filter:</strong> ${rules.language_filter.join(', ')}</p>` : '<p class="text-muted">No language filter</p>'}
            </div>
        </div>
    `;
}

function renderLaunch() {
    const channels = wizardState.channels || [];
    return `
        <div class="text-center py-4">
            <i class="bi bi-rocket-takeoff" style="font-size:3rem;color:#198754;"></i>
            <h3 class="mt-3">Ready to Launch!</h3>
            <p class="text-muted mt-2">
                Your automated backlink publishing system is configured with
                <strong>${channels.length} channel(s)</strong>
                and will scan for new targets every
                <strong>${wizardState.polling_interval_seconds / 3600} hour(s)</strong>.
            </p>
            <p class="text-muted">
                Click "Start Automation" to begin. The system will immediately run
                its first scan and enqueue any new targets found.
            </p>
        </div>
    `;
}

function collectStepData(step) {
    switch (step) {
        case 2: {
            const inputs = document.querySelectorAll('.sitemap-input');
            wizardState.sitemap_urls = Array.from(inputs).map(inp => inp.value).filter(v => v.trim());
            wizardState.manual_targets = qs('#manual-targets')?.value || '';
            wizardState.bookmark_url = qs('#bookmark-url')?.value || '';
            return { sitemap_urls: wizardState.sitemap_urls, manual_targets: wizardState.manual_targets, bookmark_url: wizardState.bookmark_url };
        }
        case 3: {
            const checkedChannels = document.querySelectorAll('.channel-checkbox:checked');
            wizardState.channels = Array.from(checkedChannels).map(cb => {
                const name = cb.dataset.channel;
                const capInput = document.querySelector(`input[data-channel="${name}"][data-field="daily_cap"]`);
                return {
                    channel: name,
                    bound: false,
                    daily_cap: capInput ? parseInt(capInput.value, 10) || 10 : 10,
                    dofollow_preference: true,
                    language_whitelist: [],
                };
            });
            return { channels: wizardState.channels };
        }
        case 4: {
            const intervalSelect = qs('#polling-interval');
            if (intervalSelect) wizardState.polling_interval_seconds = parseInt(intervalSelect.value, 10) || 21600;
            const capInput = qs('#default-daily-cap');
            if (capInput) wizardState.default_daily_cap = parseInt(capInput.value, 10) || 10;
            const maxInput = qs('#max-daily-publish');
            if (maxInput) wizardState.max_daily_publish = parseInt(maxInput.value, 10) || 50;
            const langFilter = qs('#language-filter');
            wizardState.language_filter = langFilter ? langFilter.value.split(',').map(s => s.trim()).filter(Boolean) : [];
            return {
                polling_interval_seconds: wizardState.polling_interval_seconds,
                default_daily_cap: wizardState.default_daily_cap,
                max_daily_publish: wizardState.max_daily_publish,
                language_filter: wizardState.language_filter,
            };
        }
        default:
            return {};
    }
}

async function submitStep(step) {
    const data = collectStepData(step);
    let endpoint = '';
    switch (step) {
        case 2: endpoint = '/wizard/step/seed-sources'; break;
        case 3: endpoint = '/wizard/step/channels'; break;
        case 4: endpoint = '/wizard/step/rules'; break;
    }
    if (!endpoint) return true;
    try {
        const resp = await postJson(endpoint, data);
        return resp && resp.ok;
    } catch (e) {
        console.error('Wizard step submission failed:', e);
        return false;
    }
}

async function handleLaunch() {
    const launchBtn = qs('#wizard-launch');
    launchBtn.disabled = true;
    launchBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Starting...';
    try {
        const resp = await postJson('/wizard/step/launch', {});
        if (resp && resp.status === 'active') {
            window.location.href = resp.redirect || '/';
        } else {
            alert('Failed to start automation. Please try again.');
            launchBtn.disabled = false;
            launchBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i> Start Automation';
        }
    } catch (e) {
        alert('Failed to start automation: ' + e.message);
        launchBtn.disabled = false;
        launchBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i> Start Automation';
    }
}

async function goToStep(step) {
    if (step > currentStep) {
        // Submitting current step before advancing
        const ok = await submitStep(currentStep);
        if (!ok) return;
    }
    if (step < 1) step = 1;
    if (step > TOTAL_STEPS) step = TOTAL_STEPS;
    currentStep = step;
    renderStep(currentStep);
}

function handleSkip() {
    if (confirm('Skip the setup wizard? You can configure everything later in Settings.')) {
        window.location.href = '/';
    }
}

function initEventListeners() {
    const wizardRoot = qs('#wizard-app');
    if (!wizardRoot) return;

    delegate(wizardRoot, 'click', '[data-wizard-action="next"]', () => goToStep(currentStep + 1));
    delegate(wizardRoot, 'click', '[data-wizard-action="prev"]', () => goToStep(currentStep - 1));
    delegate(wizardRoot, 'click', '[data-wizard-action="skip"]', handleSkip);
    delegate(wizardRoot, 'click', '[data-wizard-action="launch"]', handleLaunch);

    delegate(wizardRoot, 'click', '#add-sitemap-btn', () => {
        const list = qs('#sitemap-list');
        const idx = document.querySelectorAll('.sitemap-input').length;
        const emptyMsg = list.querySelector('.text-muted.small');
        if (emptyMsg) emptyMsg.remove();
        const div = document.createElement('div');
        div.className = 'input-group mb-2';
        div.innerHTML = `
            <input type="url" class="form-control form-control-sm sitemap-input" placeholder="https://example.com/sitemap.xml" data-index="${idx}">
            <button class="btn btn-outline-danger btn-sm remove-sitemap" type="button" data-index="${idx}">
                <i class="bi bi-x"></i>
            </button>
        `;
        list.appendChild(div);
    });

    delegate(wizardRoot, 'click', '.remove-sitemap', (_e, btn) => {
        const group = btn.closest('.input-group');
        if (group) group.remove();
    });

    delegate(wizardRoot, 'click', '[data-action="toggle-channel"]', (_e, item) => {
        const cb = item.querySelector('.channel-checkbox');
        if (cb) {
            cb.checked = !cb.checked;
            item.classList.toggle('active', cb.checked);
            // Re-render channels to show/hide options
            renderStep(currentStep);
        }
    });

    delegate(wizardRoot, 'change', '.channel-checkbox', (_e, cb) => {
        const item = cb.closest('[data-channel]');
        if (item) {
            item.classList.toggle('active', cb.checked);
        }
    });
}

// Bootstrap
document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    renderStep(currentStep);
});
