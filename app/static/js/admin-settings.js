// app/static/js/admin-settings.js
// Settings tab: renders every admin-editable parameter from the server schema.
// A saved value lives in the database and overrides .env; "reset" drops it.
(function () {
    'use strict';

    const searchInput = document.getElementById('settings-search');
    const saveButton = document.getElementById('settings-save');
    const statusBox = document.getElementById('settings-status');
    const groupsBox = document.getElementById('settings-groups');
    if (!searchInput || !saveButton || !groupsBox) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    const dirty = new Map();
    let schema = [];

    function humanize(key) {
        return key.toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    function setStatus(text, isError) {
        statusBox.textContent = text || '';
        statusBox.classList.toggle('is-error', !!isError);
    }

    function markDirty() {
        saveButton.disabled = dirty.size === 0;
    }

    function buildField(field) {
        const row = document.createElement('div');
        row.className = 'settings-field';
        row.dataset.key = field.key;

        const label = document.createElement('label');
        label.className = 'settings-field-label';
        label.textContent = humanize(field.key);
        label.title = field.key;
        label.htmlFor = 'setting-' + field.key;
        row.appendChild(label);

        let input;
        if (field.kind === 'bool') {
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = field.value === true;
        } else {
            input = document.createElement('input');
            input.type = field.secret ? 'password' : (field.kind === 'int' || field.kind === 'float' ? 'number' : 'text');
            if (field.kind === 'int') input.step = '1';
            if (field.kind === 'float') input.step = 'any';
            if (field.secret) {
                input.placeholder = field.has_value ? '••••••' : '';
                input.autocomplete = 'new-password';
            } else {
                input.value = field.value === null || field.value === undefined ? '' : String(field.value);
            }
        }
        input.id = 'setting-' + field.key;
        input.className = 'settings-input';
        input.dataset.kind = field.kind;
        input.addEventListener('change', () => {
            if (field.secret) {
                if (!input.value) return; // an empty secret field means "leave unchanged"
                dirty.set(field.key, input.value);
            } else if (field.kind === 'bool') {
                dirty.set(field.key, input.checked);
            } else {
                dirty.set(field.key, input.value);
            }
            markDirty();
        });
        row.appendChild(input);

        const meta = document.createElement('span');
        meta.className = 'settings-default';
        if (field.secret) {
            meta.textContent = field.has_value ? t('settings_secret_set') : t('settings_secret_empty');
        } else {
            meta.textContent = t('settings_default') + ': ' + String(field.default);
        }
        row.appendChild(meta);

        const reset = document.createElement('button');
        reset.type = 'button';
        reset.className = 'settings-reset';
        reset.textContent = '↺';
        reset.title = t('settings_reset');
        reset.addEventListener('click', () => resetField(field.key));
        row.appendChild(reset);

        return row;
    }

    function render() {
        groupsBox.innerHTML = '';
        schema.forEach((group) => {
            const details = document.createElement('details');
            details.className = 'settings-group';
            details.open = true;
            details.dataset.group = group.id;

            const summary = document.createElement('summary');
            summary.textContent = t(group.label) || group.label;
            const count = document.createElement('span');
            count.className = 'settings-count';
            count.textContent = group.fields.length;
            summary.appendChild(count);
            details.appendChild(summary);

            const grid = document.createElement('div');
            grid.className = 'settings-grid';
            group.fields.forEach((field) => grid.appendChild(buildField(field)));
            details.appendChild(grid);
            groupsBox.appendChild(details);
        });
        applyFilter();
    }

    function applyFilter() {
        const needle = searchInput.value.trim().toLowerCase();
        groupsBox.querySelectorAll('.settings-group').forEach((group) => {
            let visible = 0;
            group.querySelectorAll('.settings-field').forEach((field) => {
                const key = field.dataset.key.toLowerCase();
                const match = !needle || key.includes(needle) || humanize(field.dataset.key).toLowerCase().includes(needle);
                field.hidden = !match;
                if (match) visible += 1;
            });
            group.hidden = visible === 0;
            if (needle) group.open = true;
        });
    }

    function load() {
        return fetch('/admin/api/settings', { credentials: 'same-origin' })
            .then((response) => response.json())
            .then((payload) => {
                schema = payload.groups || [];
                dirty.clear();
                markDirty();
                render();
            })
            .catch(() => setStatus(t('settings_load_error'), true));
    }

    function save() {
        if (!dirty.size) return;
        const values = {};
        dirty.forEach((value, key) => { values[key] = value; });
        setStatus(t('settings_saving'));
        fetchWithCSRF('/admin/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values: values })
        }).then((response) => response.json()).then((result) => {
            if (result.status === 'ok') {
                setStatus(t('settings_saved'));
                load();
            } else {
                setStatus(result.error || t('settings_invalid'), true);
            }
        }).catch(() => setStatus(t('settings_invalid'), true));
    }

    function resetField(key) {
        fetchWithCSRF('/admin/api/settings/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keys: [key] })
        }).then((response) => response.json()).then((result) => {
            if (result.status === 'ok') {
                setStatus(t('settings_reset_done'));
                load();
            }
        }).catch(() => setStatus(t('settings_invalid'), true));
    }

    saveButton.addEventListener('click', save);
    searchInput.addEventListener('input', applyFilter);

    const tabButton = document.querySelector('.admin-tab[data-tab="settings"]');
    if (tabButton) tabButton.addEventListener('click', load);

    document.addEventListener('DOMContentLoaded', () => {
        const tab = document.getElementById('settings-tab');
        if (tab && tab.classList.contains('active')) load();
    });

    window.AdminSettings = { load: load };
})();
