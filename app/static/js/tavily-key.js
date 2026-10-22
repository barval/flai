(function () {
    'use strict';

    const ENDPOINT = '/api-keys/tavily';

    function el(id) { return document.getElementById(id); }

    function request(url, options) {
        if (typeof fetchWithCSRF === 'function') return fetchWithCSRF(url, options);
        return fetch(url, { credentials: 'same-origin', ...options });
    }

    function setStatus(text) {
        const status = el('tavily-status');
        if (!status) return;
        status.textContent = text || '';
        status.hidden = !text;
        status.className = 'tavily-status' + (text ? ' is-visible' : '');
    }

    // Chat-style live status while the GET /api-keys/tavily round-trip is in
    // flight («Updating information (N s)…»): pulsing + elapsed seconds.
    // Without it the panel first showed the add-key form, then re-rendered to
    // the saved-key branch a few seconds later (a visible flicker).
    let refreshTimer = null;
    let refreshStartedAt = null;

    function stopRefreshStatus() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
        refreshStartedAt = null;
        const status = el('tavily-status');
        if (status) status.classList.remove('tavily-status-live');
    }

    function startRefreshStatus() {
        stopRefreshStatus();
        const status = el('tavily-status');
        const section = el('tavily-section');
        if (!status || !section) return;
        refreshStartedAt = Date.now();
        const base = '🔄 ' + (section.dataset.refreshingLabel || 'Refreshing…');
        const suffix = section.dataset.elapsedSuffix || 's';
        const paint = () => {
            const secs = Math.floor((Date.now() - refreshStartedAt) / 1000);
            status.textContent = secs >= 1 ? base + ' (' + secs + suffix + ')…' : base + '…';
        };
        paint();
        status.hidden = false;
        status.className = 'tavily-status is-visible tavily-status-live';
        refreshTimer = setInterval(() => {
            if (status.hidden || document.body.contains(status) === false) {
                stopRefreshStatus();
                return;
            }
            paint();
        }, 1000);
    }

    function showLoadingState() {
        // Hide BOTH branches until the server answers.
        const input = el('tavily-key-input');
        const addRow = el('tavily-create');
        const savedRow = el('tavily-key-saved');
        const quota = el('tavily-quota');
        if (addRow) addRow.hidden = true;
        if (savedRow) savedRow.hidden = true;
        if (quota) quota.hidden = true;
        if (input) input.value = '';
        startRefreshStatus();
    }

    function formatQuota(template, usage) {
        return template
            .replace('{limit}', usage.limit ?? '\u2014')
            .replace('{used}', usage.used ?? '\u2014')
            .replace('{remaining}', usage.remaining ?? '\u2014');
    }

    function render(data) {
        stopRefreshStatus();
        const input = el('tavily-key-input');
        const addRow = el('tavily-create');
        const savedRow = el('tavily-key-saved');
        const masked = el('tavily-key-masked');
        const quota = el('tavily-quota');
        if (!input || !addRow || !savedRow || !masked || !quota) return;

        if (data.has_key) {
            addRow.hidden = true;
            input.hidden = false; // re-show for the next no-key render
            savedRow.hidden = false;
            masked.textContent = data.masked_key || '';
            quota.textContent = formatQuota(el('tavily-section').dataset.quotaTemplate || '', data);
            quota.hidden = false;
            if (data.status === 'exhausted') setStatus(el('tavily-section').dataset.exhaustedLabel);
            else if (data.status === 'invalid') setStatus(el('tavily-section').dataset.invalidLabel);
            else if (data.status === 'unavailable') setStatus(el('tavily-section').dataset.unavailableLabel);
            else setStatus('');
        } else {
            input.hidden = false;
            input.value = '';
            addRow.hidden = false;
            savedRow.hidden = true;
            quota.hidden = true;
            setStatus('');
        }
    }

    async function load() {
        showLoadingState();
        const response = await request(ENDPOINT);
        if (!response.ok) {
            stopRefreshStatus();
            setStatus(el('tavily-section') ? el('tavily-section').dataset.unavailableLabel : '');
            // Fall back to the no-key form so the section stays usable.
            render({ has_key: false });
            return;
        }
        render(await response.json());
    }

    async function save(value) {
        const response = await request(ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: value }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            setStatus(data.error || '');
            return;
        }
        render(data);
    }

    async function remove() {
        const delBtn = el('tavily-key-del-btn');
        if (delBtn) delBtn.disabled = true;
        try {
            const response = await request(ENDPOINT, { method: 'DELETE' });
            if (!response.ok) return;
            render(await response.json());
        } finally {
            if (delBtn) delBtn.disabled = false;
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const panel = el('api-keys-panel');
        const trigger = el('api-keys-btn');
        const input = el('tavily-key-input');
        const addBtn = el('tavily-key-add-btn');
        const delBtn = el('tavily-key-del-btn');
        if (!panel || !trigger || !input || !addBtn || !delBtn) return;

        addBtn.addEventListener('click', () => {
            if (input.value.trim()) save(input.value.trim());
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && input.value.trim()) {
                event.preventDefault();
                save(input.value.trim());
            }
        });
        delBtn.addEventListener('click', remove);

        trigger.addEventListener('click', () => {
            if (!panel.hidden) load();
        });

        // Cleanup when the panel closes (toggle handler hides it).
        new MutationObserver(() => {
            if (panel.hidden) stopRefreshStatus();
        }).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
    });
})();