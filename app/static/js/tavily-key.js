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

    function formatQuota(template, usage) {
        return template
            .replace('{limit}', usage.limit ?? '\u2014')
            .replace('{used}', usage.used ?? '\u2014')
            .replace('{remaining}', usage.remaining ?? '\u2014');
    }

    function render(data) {
        const input = el('tavily-key-input');
        const addRow = el('tavily-create');
        const savedRow = el('tavily-saved');
        const masked = el('tavily-key-masked');
        const quota = el('tavily-quota');
        if (!input || !addRow || !savedRow || !masked || !quota) return;

        if (data.has_key) {
            addRow.hidden = true;
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
            setStatus('');
        }
    }

    async function load() {
        const response = await request(ENDPOINT);
        if (!response.ok) return;
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
        const response = await request(ENDPOINT, { method: 'DELETE' });
        if (!response.ok) return;
        render(await response.json());
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
    });
})();