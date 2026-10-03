(function () {
    'use strict';

    const ENDPOINT = '/api-keys/tavily';

    function el(id) { return document.getElementById(id); }

    function request(url, options = {}) {
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
        const section = el('tavily-section');
        const input = el('tavily-key-input');
        const action = el('tavily-key-action');
        const saved = el('tavily-key-saved');
        const masked = el('tavily-key-masked');
        const quota = el('tavily-quota');
        if (!section || !input || !action || !saved || !masked || !quota) return;

        const labels = {
            add: section.dataset.addLabel,
            remove: section.dataset.deleteLabel,
            invalid: section.dataset.invalidLabel,
            exhausted: section.dataset.exhaustedLabel,
            unavailable: section.dataset.unavailableLabel,
        };

        if (data.has_key) {
            input.hidden = true;
            action.textContent = labels.remove;
            action.classList.add('tavily-delete');
            saved.hidden = false;
            masked.textContent = data.masked_key || '';
            quota.textContent = formatQuota(section.dataset.quotaTemplate || '', data);
            quota.hidden = false;
            if (data.status === 'exhausted') setStatus(labels.exhausted);
            else if (data.status === 'invalid') setStatus(labels.invalid);
            else if (data.status === 'unavailable') setStatus(labels.unavailable);
            else setStatus('');
        } else {
            input.hidden = false;
            input.value = '';
            action.textContent = labels.add;
            action.classList.remove('tavily-delete');
            saved.hidden = true;
            quota.hidden = true;
            setStatus('');
        }
        section.dataset.hasKey = data.has_key ? '1' : '0';
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
        const section = el('tavily-section');
        const panel = el('api-keys-panel');
        const trigger = el('api-keys-btn');
        const action = el('tavily-key-action');
        const input = el('tavily-key-input');
        if (!section || !panel || !trigger || !action || !input) return;

        action.addEventListener('click', () => {
            if (section.dataset.hasKey === '1') {
                remove();
            } else if (input.value.trim()) {
                save(input.value.trim());
            }
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && input.value.trim()) {
                event.preventDefault();
                save(input.value.trim());
            }
        });

        trigger.addEventListener('click', () => {
            if (!panel.hidden) load();
        });
    });
})();