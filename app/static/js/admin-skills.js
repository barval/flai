// app/static/js/admin-skills.js
// Skills tab: install, edit, enable and remove the instructions the model can load.
//
// Two zones, and the panel says which is which: a bundled skill ships with the
// project inside git, so it can be switched off but never written — a save there
// would dirty the working tree. An installed one lives under data/skills and is
// fully editable. Labels come from window.TRANSLATIONS, which the template fills
// from the catalogs, so nothing here is an untranslated literal.
(function () {
    'use strict';

    const body = document.getElementById('skills-body');
    if (!body) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    const status = document.getElementById('skills-status');
    const editor = document.getElementById('skill-editor');
    const nameInput = document.getElementById('skill-name');
    const bodyEn = document.getElementById('skill-body-en');
    const descriptionRu = document.getElementById('skill-description-ru');
    const bodyRu = document.getElementById('skill-body-ru');
    const editorStatus = document.getElementById('skill-editor-status');
    const uploadInput = document.getElementById('skills-upload');
    const uploadButton = document.getElementById('skills-upload-button');
    const newButton = document.getElementById('skills-new');
    const saveButton = document.getElementById('skill-save');
    const cancelButton = document.getElementById('skill-cancel');

    let skills = [];
    let editing = null;

    function setStatus(node, text, isError) {
        if (!node) return;
        node.textContent = text || '';
        node.classList.toggle('is-error', !!isError);
    }

    async function api(path, options) {
        // Every write on this tab is CSRF-protected; a bare fetch() is a 400 in a
        // real browser, which the test suite cannot see because it disables CSRF.
        const response = await fetchWithCSRF(path, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || 'HTTP ' + response.status);
        return payload;
    }

    function markLabel(mark) {
        if (mark.status === 'offered') return null;
        if (mark.status === 'typo') return t('Unknown tool') + ': ' + mark.name + ' → ' + mark.suggestion;
        if (mark.status === 'unavailable') return mark.name + ' — ' + t('Not available here');
        return mark.name + ' — ' + t('Not available here');
    }

    function cell(row, text, label) {
        const td = document.createElement('td');
        if (label) td.dataset.label = label;
        if (text instanceof Node) td.appendChild(text);
        else td.textContent = text;
        row.appendChild(td);
        return td;
    }

    function render() {
        body.innerHTML = '';
        const empty = document.getElementById('skills-empty');
        if (empty) empty.style.display = skills.length ? 'none' : '';
        skills.forEach((skill) => {
            const row = document.createElement('tr');
            row.className = 'skill-row';

            cell(row, skill.name, t('Name'));

            const description = document.createElement('td');
            description.dataset.label = t('Description');
            description.textContent = skill.description;
            if (skill.shadowed) {
                const note = document.createElement('div');
                note.className = 'skill-note';
                note.textContent = t('Shadowed by a bundled skill');
                description.appendChild(note);
            }
            row.appendChild(description);

            const source = document.createElement('td');
            source.dataset.label = t('Source');
            const badge = document.createElement('span');
            badge.className = 'skill-badge skill-badge--' + skill.source;
            badge.textContent = skill.source === 'bundled' ? t('Bundled') : t('Installed');
            source.appendChild(badge);
            row.appendChild(source);

            const tools = document.createElement('td');
            tools.dataset.label = t('Tools');
            (skill.allowed_tools || []).forEach((mark) => {
                const line = document.createElement('div');
                const label = markLabel(mark);
                line.className = 'tool-mark tool-mark--' + mark.status;
                line.textContent = label || mark.name;
                tools.appendChild(line);
            });
            row.appendChild(tools);

            const enabled = document.createElement('td');
            enabled.dataset.label = t('Enabled');
            const toggle = document.createElement('input');
            toggle.type = 'checkbox';
            toggle.checked = !!skill.enabled;
            toggle.addEventListener('change', () => setEnabled(skill, toggle));
            enabled.appendChild(toggle);
            row.appendChild(enabled);

            const actions = document.createElement('td');
            actions.dataset.label = t('Actions');
            if (skill.source === 'bundled') {
                const readOnly = document.createElement('span');
                readOnly.className = 'skill-note';
                readOnly.textContent = t('Read-only');
                actions.appendChild(readOnly);
            } else {
                const edit = document.createElement('button');
                edit.className = 'btn-small';
                edit.textContent = skill.shadowed ? t('Rename') : t('Edit');
                edit.addEventListener('click', () => openEditor(skill));
                actions.appendChild(edit);

                const remove = document.createElement('button');
                remove.className = 'btn-small btn-danger';
                remove.textContent = t('Delete');
                remove.addEventListener('click', () => removeSkill(skill));
                actions.appendChild(remove);
            }
            row.appendChild(actions);

            body.appendChild(row);
        });
    }

    async function load() {
        try {
            const payload = await api('/admin/api/skills');
            skills = payload.skills || [];
            render();
        } catch (error) {
            setStatus(status, error.message, true);
        }
    }

    async function setEnabled(skill, checkbox) {
        try {
            await api('/admin/api/skills/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: skill.name, enabled: checkbox.checked }),
            });
            skill.enabled = checkbox.checked;
            setStatus(status, checkbox.checked ? t('Skill enabled') : t('Skill disabled'));
        } catch (error) {
            checkbox.checked = !checkbox.checked;
            setStatus(status, error.message, true);
        }
    }

    async function removeSkill(skill) {
        if (!window.confirm(skill.name + ' — ' + t('Delete') + '?')) return;
        try {
            await api('/admin/api/skills/' + encodeURIComponent(skill.name), { method: 'DELETE' });
            setStatus(status, t('Skill deleted'));
            await load();
        } catch (error) {
            setStatus(status, error.message, true);
        }
    }

    function openEditor(skill) {
        editing = skill || { name: '', body: '', description_ru: '', body_ru: '' };
        nameInput.value = editing.name;
        bodyEn.value = editing.body || '';
        descriptionRu.value = editing.description_ru || '';
        bodyRu.value = editing.body_ru || '';
        setStatus(editorStatus, '');
        editor.hidden = false;
        bodyEn.focus();
    }

    function closeEditor() {
        editing = null;
        editor.hidden = true;
    }

    async function save() {
        if (!editing) return;
        const name = nameInput.value.trim();
        try {
            const payload = await api('/admin/api/skills/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    body_en: bodyEn.value,
                    description_ru: descriptionRu.value,
                    body_ru: bodyRu.value,
                }),
            });
            const warnings = payload.warnings || [];
            // A rename is a save under the new name; the old folder is removed
            // only after that save succeeded, and only for an installed skill.
            if (editing.name && editing.name !== name && editing.source !== 'bundled') {
                await api('/admin/api/skills/' + encodeURIComponent(editing.name), { method: 'DELETE' });
            }
            setStatus(status, warnings.length ? warnings.join(' · ') : t('Skill saved'), warnings.length > 0);
            closeEditor();
            await load();
        } catch (error) {
            setStatus(editorStatus, error.message, true);
        }
    }

    async function upload(file) {
        const data = new FormData();
        data.append('file', file);
        try {
            const payload = await api('/admin/api/skills/upload', { method: 'POST', body: data });
            const warnings = payload.warnings || [];
            setStatus(status, warnings.length ? warnings.join(' · ') : t('Skill saved'), warnings.length > 0);
            await load();
        } catch (error) {
            setStatus(status, error.message, true);
        }
    }

    if (uploadButton && uploadInput) {
        uploadButton.addEventListener('click', () => uploadInput.click());
        uploadInput.addEventListener('change', () => {
            if (uploadInput.files && uploadInput.files[0]) upload(uploadInput.files[0]);
            uploadInput.value = '';
        });
    }
    if (newButton) newButton.addEventListener('click', () => openEditor(null));
    if (saveButton) saveButton.addEventListener('click', save);
    if (cancelButton) cancelButton.addEventListener('click', closeEditor);
    document.addEventListener('DOMContentLoaded', load);
})();
