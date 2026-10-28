// app/static/js/chat-scope.js
// The search scope is a bulk selector for the chat corpus. The corpus itself is
// the set of documents ticked in the Documents panel; this popover only ticks
// documents by folder (several at once, including "Unfiled") and upload dates.
// Nothing is sent as a separate filter: the selection becomes doc_ids, so chat,
// deep analysis and tasks all search exactly the same snapshot.
(function () {
    'use strict';

    const STORAGE_KEY = 'chat_scope';
    const ROOT_GROUP = '__root__';
    const toggle = document.getElementById('scope-toggle');
    const popover = document.getElementById('scope-popover');
    const label = document.getElementById('scope-label');
    const folderList = document.getElementById('scope-folders');
    const fromInput = document.getElementById('scope-from');
    const toInput = document.getElementById('scope-to');
    const applyButton = document.getElementById('scope-apply');
    const clearButton = document.getElementById('scope-clear');
    if (!toggle || !popover) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    const fill = (template, count) => template.replace('{count}', count);
    let filter = { folders: [], unfiled: false, from: '', to: '' };
    let foldersLoaded = false;

    try {
        const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}') || {};
        filter = {
            folders: Array.isArray(saved.folders) ? saved.folders : [],
            unfiled: !!saved.unfiled,
            from: saved.from || '',
            to: saved.to || '',
        };
    } catch (err) {
        filter = { folders: [], unfiled: false, from: '', to: '' };
    }

    // The chip describes the corpus, not the filter: count and, when the whole
    // selection came from complete folders, how many groups it spans.
    function renderChip() {
        const summary = (typeof window.getCorpusSummary === 'function')
            ? window.getCorpusSummary()
            : { count: 0, groups: [] };
        const count = Number(summary.count) || 0;
        const groups = Array.isArray(summary.groups) ? summary.groups : [];
        const documents = fill(t('documents ({count})'), count);
        let text;
        if (count === 0) {
            text = '📄 ' + t('All documents');
        } else if (groups.length === 1) {
            text = '📁 ' + groups[0] + ' · 📄 ' + documents;
        } else if (groups.length > 1) {
            text = '📁 ' + fill(t('folders ({count})'), groups.length) + ' · 📄 ' + documents;
        } else {
            text = '📄 ' + documents;
        }
        label.textContent = text;
        toggle.title = groups.length ? groups.join(', ') + ' · ' + documents : t('Search scope');
        toggle.classList.toggle('is-active', count > 0);
    }
    window.renderCorpusChip = renderChip;

    function persistFilter() {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filter));
    }

    function renderFolders(payload) {
        if (!folderList) return;
        folderList.innerHTML = '';
        const documents = payload.documents || [];
        const folders = (payload.folders || []).filter((folder) => (folder.count || 0) > 0);
        const hasRoot = documents.some((doc) => !doc.folder_id);
        const options = hasRoot ? [{ id: ROOT_GROUP, name: t('Unfiled') }] : [];
        folders.forEach((folder) => options.push({ id: folder.id, name: folder.name }));
        if (!options.length) {
            const empty = document.createElement('span');
            empty.className = 'scope-folders-empty';
            empty.textContent = t('All documents');
            folderList.appendChild(empty);
            return;
        }
        options.forEach((option) => {
            const row = document.createElement('label');
            row.className = 'scope-folder-option';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = option.id;
            box.checked = option.id === ROOT_GROUP ? filter.unfiled : filter.folders.includes(option.id);
            row.appendChild(box);
            row.appendChild(document.createTextNode(' ' + option.name));
            folderList.appendChild(row);
        });
    }

    function loadFolders() {
        if (foldersLoaded) return;
        foldersLoaded = true;
        fetch('/api/documents', { credentials: 'same-origin' })
            .then((response) => response.json())
            .then((payload) => {
                renderFolders(payload);
                renderChip();
            })
            .catch(() => {});
    }

    // The popover is a fixed layer clamped to the viewport: on a phone it
    // becomes a bottom sheet, on a desktop it opens above the button and
    // slides left when it would cross the right edge. Nothing can widen the
    // page, whatever the container the button lives in.
    function positionPopover() {
        if (window.innerWidth <= 768) {
            popover.style.left = '8px';
            popover.style.right = '8px';
            popover.style.width = 'auto';
            popover.style.bottom = '8px';
            popover.style.top = 'auto';
            return;
        }
        const width = Math.min(320, window.innerWidth - 16);
        const rect = toggle.getBoundingClientRect();
        const height = popover.offsetHeight || 0;
        const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
        let top = rect.top - height - 8;
        if (top < 8) top = rect.bottom + 8;
        top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
        popover.style.width = width + 'px';
        popover.style.left = left + 'px';
        popover.style.top = top + 'px';
        popover.style.right = 'auto';
        popover.style.bottom = 'auto';
    }

    function closePopover() {
        popover.hidden = true;
    }

    toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        if (popover.hidden) {
            loadFolders();
            fromInput.value = filter.from || '';
            toInput.value = filter.to || '';
            popover.hidden = false;
            positionPopover();
        } else {
            closePopover();
        }
    });

    document.addEventListener('click', (event) => {
        if (!popover.hidden && !popover.contains(event.target) && event.target !== toggle) {
            closePopover();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !popover.hidden) {
            closePopover();
        }
    });

    window.addEventListener('resize', () => {
        if (!popover.hidden) positionPopover();
    });

    // A fixed layer over a scrolling page drifts away from its anchor.
    window.addEventListener('scroll', () => {
        if (!popover.hidden) closePopover();
    }, true);

    applyButton.addEventListener('click', () => {
        const checked = folderList
            ? Array.from(folderList.querySelectorAll('input:checked')).map((box) => box.value)
            : [];
        const folders = checked.filter((value) => value !== ROOT_GROUP);
        const unfiled = checked.includes(ROOT_GROUP);
        if (!folders.length && !unfiled) {
            alert(t('All documents'));
            return;
        }
        const count = (typeof window.applyCorpusFilter === 'function')
            ? window.applyCorpusFilter({
                folderIds: folders,
                unfiled: unfiled,
                from: fromInput.value,
                to: toInput.value,
            })
            : 0;
        if (!count) {
            alert(t('No documents match the selected folders and dates.'));
            return;
        }
        filter = { folders: folders, unfiled: unfiled, from: fromInput.value, to: toInput.value };
        persistFilter();
        renderChip();
        closePopover();
    });

    clearButton.addEventListener('click', () => {
        filter = { folders: [], unfiled: false, from: '', to: '' };
        persistFilter();
        fromInput.value = '';
        toInput.value = '';
        if (folderList) {
            folderList.querySelectorAll('input').forEach((box) => { box.checked = false; });
        }
        if (typeof window.clearCorpusFilter === 'function') window.clearCorpusFilter();
        renderChip();
        closePopover();
    });

    // Keep the chip honest when a document is added or deleted in another tab.
    renderChip();
    loadFolders();
})();
