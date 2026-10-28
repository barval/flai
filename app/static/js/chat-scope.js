// app/static/js/chat-scope.js
// The search scope is a bulk selector for the chat corpus. The corpus itself is
// the set of documents ticked in the Documents panel; this popover edits that
// same set live — ticking a folder/root ticks its documents, and a partly ticked
// folder shows the indeterminate state. Nothing is sent as a separate filter:
// the selection becomes doc_ids, so chat, deep analysis and tasks all search
// exactly the same snapshot.
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
    const clearButton = document.getElementById('scope-clear');
    if (!toggle || !popover) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    const fill = (template, count) => template.replace('{count}', count);
    let filter = { from: '', to: '' };
    let groupDocs = {};   // group id -> document ids
    let groupBoxes = {};  // group id -> checkbox
    let docsById = {};    // document id -> document (for uploaded_at)

    try {
        const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}') || {};
        filter = { from: saved.from || '', to: saved.to || '' };
    } catch (err) {
        filter = { from: '', to: '' };
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
        let groupText;
        let countText;
        if (count === 0) {
            groupText = '📄 ' + t('All documents');
            countText = '';
        } else if (groups.length === 1) {
            groupText = '📁 ' + groups[0];
            countText = '· 📄 ' + documents;
        } else if (groups.length > 1) {
            groupText = '📁 ' + fill(t('folders ({count})'), groups.length);
            countText = '· 📄 ' + documents;
        } else {
            groupText = '';
            countText = '📄 ' + documents;
        }
        // Two spans: the group part may ellipsize, the document count never does.
        label.textContent = '';
        const groupSpan = document.createElement('span');
        groupSpan.className = 'scope-label-groups';
        groupSpan.textContent = groupText;
        const countSpan = document.createElement('span');
        countSpan.className = 'scope-label-count';
        countSpan.textContent = countText;
        label.appendChild(groupSpan);
        label.appendChild(countSpan);
        toggle.title = groups.length ? groups.join(', ') + ' · ' + documents : t('Search scope');
        toggle.classList.toggle('is-active', count > 0);
    }
    window.renderCorpusChip = renderChip;

    function persistFilter() {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filter));
    }

    function selectedIds() {
        return new Set((typeof window.getSelectedDocIds === 'function') ? window.getSelectedDocIds() : []);
    }

    // A document is eligible only inside the active upload-date window.
    function inRange(doc) {
        const stamp = Date.parse((doc && doc.uploaded_at) || '');
        const from = filter.from ? new Date(filter.from + 'T00:00:00').getTime() : null;
        const to = filter.to ? new Date(filter.to + 'T23:59:59.999').getTime() : null;
        if (from !== null && !(stamp >= from)) return false;
        if (to !== null && !(stamp <= to)) return false;
        return true;
    }

    function eligible(groupId) {
        return (groupDocs[groupId] || []).filter((id) => inRange(docsById[id]));
    }

    // Tri-state mirror: checked = every eligible document of the group is
    // ticked, indeterminate = only a part of it.
    function syncCheckboxes() {
        const selected = selectedIds();
        Object.keys(groupBoxes).forEach((groupId) => {
            const ids = eligible(groupId);
            const picked = ids.filter((id) => selected.has(id)).length;
            const box = groupBoxes[groupId];
            box.checked = ids.length > 0 && picked === ids.length;
            box.indeterminate = picked > 0 && picked < ids.length;
        });
    }

    function applySelection(ids) {
        if (typeof window.setCorpusSelection === 'function') {
            window.setCorpusSelection(ids);
        }
        syncCheckboxes();
    }

    function renderFolders(payload) {
        if (!folderList) return;
        folderList.innerHTML = '';
        groupDocs = {};
        groupBoxes = {};
        docsById = {};
        const documents = payload.documents || [];
        documents.forEach((doc) => { docsById[doc.id] = doc; });

        const entries = [];
        const rootIds = documents.filter((doc) => !doc.folder_id).map((doc) => doc.id);
        if (rootIds.length) entries.push({ id: ROOT_GROUP, name: t('Unfiled'), docs: rootIds });
        (payload.folders || []).filter((folder) => (folder.count || 0) > 0).forEach((folder) => {
            const ids = documents.filter((doc) => doc.folder_id === folder.id).map((doc) => doc.id);
            if (ids.length) entries.push({ id: folder.id, name: folder.name, docs: ids });
        });

        if (!entries.length) {
            const empty = document.createElement('span');
            empty.className = 'scope-folders-empty';
            empty.textContent = t('All documents');
            folderList.appendChild(empty);
            return;
        }

        entries.forEach((entry) => {
            const row = document.createElement('label');
            row.className = 'scope-folder-option';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = entry.id;
            row.appendChild(box);
            row.appendChild(document.createTextNode(' ' + entry.name));
            groupDocs[entry.id] = entry.docs;
            groupBoxes[entry.id] = box;
            box.addEventListener('change', () => {
                const current = selectedIds();
                if (box.checked) {
                    eligible(entry.id).forEach((id) => current.add(id));
                } else {
                    entry.docs.forEach((id) => current.delete(id));
                }
                applySelection(Array.from(current));
            });
            folderList.appendChild(row);
        });
        syncCheckboxes();
    }

    function loadFolders() {
        // Re-read on every open: a folder or document created in the Documents
        // panel a moment ago must appear without a page reload.
        fetch('/api/documents', { credentials: 'same-origin' })
            .then((response) => response.json())
            .then((payload) => {
                renderFolders(payload);
                renderChip();
            })
            .catch(() => {});
    }

    // Dates narrow the selection inside the groups that already hold ticked
    // documents; a folder with no ticked document is not expanded by a date.
    function applyDates() {
        filter = { from: fromInput.value, to: toInput.value };
        persistFilter();
        const current = selectedIds();
        if (!current.size) return;
        const active = Object.keys(groupDocs).filter((id) => (groupDocs[id] || []).some((docId) => current.has(docId)));
        if (!active.length) return;
        const wanted = new Set();
        active.forEach((id) => eligible(id).forEach((docId) => wanted.add(docId)));
        applySelection(Array.from(wanted));
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

    fromInput.addEventListener('change', applyDates);
    toInput.addEventListener('change', applyDates);

    clearButton.addEventListener('click', () => {
        filter = { from: '', to: '' };
        persistFilter();
        fromInput.value = '';
        toInput.value = '';
        if (typeof window.clearCorpusFilter === 'function') window.clearCorpusFilter();
        syncCheckboxes();
        renderChip();
        closePopover();
    });

    // Keep the chip honest when a document is added or deleted in another tab.
    renderChip();
    loadFolders();
})();
