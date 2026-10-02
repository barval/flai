const API_KEYS_ENDPOINT = '/api-keys/tokens';

function apiKeysPanel() {
    return document.getElementById('api-keys-panel');
}

function apiKeysFormatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function apiKeysRequest(url, options = {}) {
    if (typeof fetchWithCSRF === 'function') return fetchWithCSRF(url, options);
    return fetch(url, { ...options, credentials: 'same-origin' });
}

async function apiKeysLoad() {
    const body = document.getElementById('api-keys-body');
    const panel = apiKeysPanel();
    if (!body || !panel) return;

    const response = await apiKeysRequest(API_KEYS_ENDPOINT);
    if (!response.ok) return;
    const data = await response.json();
    body.replaceChildren();

    (data.tokens || []).forEach((token) => {
        const row = document.createElement('tr');
        const nameCell = document.createElement('td');
        const prefixCell = document.createElement('td');
        const prefix = document.createElement('code');
        const createdCell = document.createElement('td');
        const usedCell = document.createElement('td');
        const actionCell = document.createElement('td');

        nameCell.textContent = token.name;
        prefix.textContent = `${token.token_prefix}…`;
        prefixCell.appendChild(prefix);
        createdCell.textContent = apiKeysFormatDate(token.created_at);
        usedCell.textContent = token.revoked_at ? '—' : apiKeysFormatDate(token.last_used_at);

        if (token.revoked_at) {
            row.classList.add('revoked');
        } else {
            const revoke = document.createElement('button');
            revoke.className = 'api-keys-revoke';
            revoke.textContent = panel.dataset.revokeLabel;
            revoke.addEventListener('click', async () => {
                if (!window.confirm(panel.dataset.revokeConfirm)) return;
                const revokeResponse = await apiKeysRequest(`${API_KEYS_ENDPOINT}/${token.id}/revoke`, {
                    method: 'POST',
                });
                if (revokeResponse.ok) apiKeysLoad();
            });
            actionCell.appendChild(revoke);
        }

        row.append(nameCell, prefixCell, createdCell, usedCell, actionCell);
        body.appendChild(row);
    });
}

async function apiKeysCreate() {
    const input = document.getElementById('api-key-name');
    const response = await apiKeysRequest(API_KEYS_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: input ? input.value.trim() : '' }),
    });
    if (!response.ok) return;

    const data = await response.json();
    const box = document.getElementById('api-key-once');
    const plaintext = document.getElementById('api-key-plaintext');
    if (box && plaintext) {
        plaintext.textContent = data.token;
        box.hidden = false;
    }
    if (input) input.value = '';
    apiKeysLoad();
}

async function apiKeysCopy() {
    const plaintext = document.getElementById('api-key-plaintext');
    if (!plaintext) return;
    try {
        await navigator.clipboard.writeText(plaintext.textContent);
    } catch {
        const range = document.createRange();
        range.selectNodeContents(plaintext);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('api-keys-btn');
    const panel = apiKeysPanel();
    if (!button || !panel) return;

    button.addEventListener('click', () => {
        panel.hidden = !panel.hidden;
        if (!panel.hidden) apiKeysLoad();
    });
    document.getElementById('api-keys-close')?.addEventListener('click', () => {
        panel.hidden = true;
    });
    document.getElementById('api-key-create')?.addEventListener('click', apiKeysCreate);
    document.getElementById('api-key-copy')?.addEventListener('click', apiKeysCopy);
});
