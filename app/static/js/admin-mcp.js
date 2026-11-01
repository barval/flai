// app/static/js/admin-mcp.js
// MCP tab: the servers whose tools the chat model may call.
//
// The values live in the same settings store as every other admin parameter, so
// this tab reads the schema once and saves through the settings endpoint — one
// precedence rule (database > .env > default) for the whole panel. A token is
// never rendered: an empty token field means "keep the stored one".
(function () {
    'use strict';

    const enabledBox = document.getElementById('mcp-enabled');
    const timeoutInput = document.getElementById('mcp-timeout');
    const saveButton = document.getElementById('mcp-save');
    const statusBox = document.getElementById('mcp-status');
    const body = document.getElementById('mcp-servers-body');
    const emptyBox = document.getElementById('mcp-empty');
    if (!enabledBox || !body) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;

    let servers = [];
    let dirty = false;

    function setStatus(text, isError) {
        if (!statusBox) return;
        statusBox.textContent = text || '';
        statusBox.classList.toggle('is-error', !!isError);
    }

    function markDirty() {
        dirty = true;
        if (saveButton) saveButton.disabled = false;
    }

    function field(className, value, placeholder, type) {
        const input = document.createElement('input');
        input.className = className;
        input.type = type || 'text';
        input.value = value === undefined || value === null ? '' : String(value);
        if (placeholder) input.placeholder = placeholder;
        if (type !== 'password') input.autocomplete = 'off';
        return input;
    }

    function render() {
        body.innerHTML = '';
        if (emptyBox) emptyBox.style.display = servers.length ? 'none' : '';
        servers.forEach((server, index) => {
            const row = document.createElement('tr');
            row.className = 'mcp-server-row';

            const nameCell = document.createElement('td');
            nameCell.dataset.label = t('Name');
            const nameInput = field('mcp-input', server.name, t('Name'));
            nameInput.addEventListener('input', () => {
                server.name = nameInput.value.trim();
                markDirty();
            });
            nameCell.appendChild(nameInput);
            row.appendChild(nameCell);

            const urlCell = document.createElement('td');
            urlCell.dataset.label = 'URL';
            const urlInput = field('mcp-input', server.url, 'https://host/mcp');
            urlInput.addEventListener('input', () => {
                server.url = urlInput.value.trim();
                markDirty();
            });
            urlCell.appendChild(urlInput);
            row.appendChild(urlCell);

            const tokenCell = document.createElement('td');
            tokenCell.dataset.label = t('Token');
            const tokenInput = field('mcp-input', '', server.token ? t('mcp_token_keep') : t('Token (optional)'), 'password');
            tokenInput.addEventListener('input', () => {
                server.newToken = tokenInput.value;
                markDirty();
            });
            tokenCell.appendChild(tokenInput);
            row.appendChild(tokenCell);

            const allowCell = document.createElement('td');
            allowCell.dataset.label = t('Allowed tools');
            const allowInput = field('mcp-input', (server.allow || []).join(', '), t('empty means all'));
            allowInput.addEventListener('input', () => {
                server.allow = allowInput.value.split(',').map((item) => item.trim()).filter(Boolean);
                markDirty();
            });
            allowCell.appendChild(allowInput);
            row.appendChild(allowCell);

            // MCP carries no user identity: a server whose answers depend on who
            // is asking cannot be offered to chat without bypassing the panel's
            // own permissions. Unchecking writes user_scoped into the entry.
            const chatCell = document.createElement('td');
            chatCell.dataset.label = t('Chat');
            const chatBox = document.createElement('input');
            chatBox.type = 'checkbox';
            chatBox.checked = !server.userScoped;
            chatBox.addEventListener('change', () => {
                server.userScoped = !chatBox.checked;
                markDirty();
            });
            chatCell.appendChild(chatBox);
            row.appendChild(chatCell);

            const toolsCell = document.createElement('td');
            toolsCell.className = 'mcp-tools';
            toolsCell.dataset.label = t('Tools');
            toolsCell.textContent = server.tools === null || server.tools === undefined
                ? '—'
                : String(server.tools.length);
            toolsCell.title = (server.tools || []).join(', ');
            if (server.error) {
                toolsCell.textContent = '⚠️';
                toolsCell.title = server.error;
            }
            row.appendChild(toolsCell);

            const actionsCell = document.createElement('td');
            actionsCell.className = 'mcp-actions';
            const testButton = document.createElement('button');
            testButton.type = 'button';
            // Same blue as every other action in the panel, with a word: two bare
            // icons side by side were one slip away from deleting a server.
            testButton.className = 'add-user-button mcp-action-button';
            testButton.textContent = t('Test connection');
            testButton.addEventListener('click', () => testServer(server, testButton));
            actionsCell.appendChild(testButton);
            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'btn-danger mcp-action-button';
            removeButton.textContent = t('Remove server');
            removeButton.addEventListener('click', () => {
                servers.splice(index, 1);
                markDirty();
                render();
            });
            actionsCell.appendChild(removeButton);
            row.appendChild(actionsCell);

            body.appendChild(row);
        });
    }

    function testServer(server, button) {
        if (!server.name || !server.url) {
            setStatus(t('A server needs a name and a URL'), true);
            return;
        }
        button.disabled = true;
        fetchWithCSRF('/admin/api/mcp/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: server.name, url: server.url, token: server.newToken || server.token || '' })
        })
            .then((response) => response.json().then((payload) => ({ ok: response.ok, payload })))
            .then(({ ok, payload }) => {
                button.disabled = false;
                if (!ok) {
                    server.error = payload.error || t('unknown_error');
                    server.tools = [];
                    setStatus(server.error, true);
                } else {
                    server.error = '';
                    server.tools = payload.tools || [];
                    setStatus(t('Tools found') + ': ' + server.tools.length, false);
                }
                render();
            })
            .catch(() => {
                button.disabled = false;
                server.error = t('unknown_error');
                setStatus(server.error, true);
                render();
            });
    }

    function currentValues() {
        const payload = servers.map((server) => {
            const entry = { name: server.name, url: server.url, allow: server.allow || [] };
            const token = server.newToken ? server.newToken : server.token;
            if (token) entry.token = token;
            if (server.userScoped) entry.user_scoped = true;
            return entry;
        });
        return {
            MCP_ENABLED: !!enabledBox.checked,
            MCP_TIMEOUT_S: String(timeoutInput ? timeoutInput.value : '10'),
            MCP_SERVERS: JSON.stringify(payload)
        };
    }

    function save() {
        if (saveButton) saveButton.disabled = true;
        setStatus(t('Saving...'), false);
        fetchWithCSRF('/admin/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values: currentValues() })
        })
            .then((response) => response.json().then((payload) => ({ ok: response.ok, payload })))
            .then(({ ok, payload }) => {
                if (!ok) {
                    setStatus(payload.error || t('settings_invalid'), true);
                    if (saveButton) saveButton.disabled = false;
                    return;
                }
                dirty = false;
                setStatus(t('Saved'), false);
                load();
            })
            .catch(() => {
                setStatus(t('unknown_error'), true);
                if (saveButton) saveButton.disabled = false;
            });
    }

    function parseServers(raw) {
        try {
            const parsed = JSON.parse(raw || '[]');
            if (!Array.isArray(parsed)) return [];
            return parsed
                .filter((item) => item && typeof item === 'object')
                .map((item) => ({
                    name: String(item.name || ''),
                    url: String(item.url || ''),
                    token: String(item.token || ''),
                    newToken: '',
                    allow: Array.isArray(item.allow) ? item.allow.map(String) : [],
                    userScoped: Boolean(item.user_scoped),
                    tools: null,
                    error: ''
                }));
        } catch (error) {
            setStatus(t('MCP servers are not valid JSON'), true);
            return [];
        }
    }

    function load() {
        fetch('/admin/api/settings')
            .then((response) => response.json())
            .then((payload) => {
                const group = (payload.groups || []).find((item) => item.id === 'mcp');
                if (!group) return;
                const values = {};
                group.fields.forEach((item) => {
                    values[item.key] = item.value;
                });
                enabledBox.checked = values.MCP_ENABLED === true;
                if (timeoutInput) timeoutInput.value = values.MCP_TIMEOUT_S === null ? 10 : values.MCP_TIMEOUT_S;
                servers = parseServers(values.MCP_SERVERS);
                dirty = false;
                if (saveButton) saveButton.disabled = true;
                render();
            })
            .catch(() => setStatus(t('unknown_error'), true));
    }

    function addServer() {
        const name = document.getElementById('mcp-new-name');
        const url = document.getElementById('mcp-new-url');
        const token = document.getElementById('mcp-new-token');
        const allow = document.getElementById('mcp-new-allow');
        const server = {
            name: name ? name.value.trim() : '',
            url: url ? url.value.trim() : '',
            token: '',
            newToken: token ? token.value : '',
            allow: allow && allow.value
                ? allow.value.split(',').map((item) => item.trim()).filter(Boolean)
                : [],
            tools: null,
            error: ''
        };
        if (!server.name || !server.url) {
            setStatus(t('A server needs a name and a URL'), true);
            return;
        }
        servers.push(server);
        [name, url, token, allow].forEach((input) => {
            if (input) input.value = '';
        });
        markDirty();
        render();
    }

    if (saveButton) saveButton.addEventListener('click', save);
    const addButton = document.getElementById('mcp-add-server');
    if (addButton) addButton.addEventListener('click', addServer);
    if (enabledBox) enabledBox.addEventListener('change', markDirty);
    if (timeoutInput) timeoutInput.addEventListener('input', markDirty);

    document.addEventListener('DOMContentLoaded', load);
    if (document.readyState !== 'loading') load();
})();
