// app/static/js/chat-scope.js
// Search scope for the RAG step: a folder and an upload-date range. The choice
// travels with every message (window.getChatScope) and is remembered per tab.
(function () {
    'use strict';

    const STORAGE_KEY = 'chat_scope';
    const toggle = document.getElementById('scope-toggle');
    const popover = document.getElementById('scope-popover');
    const label = document.getElementById('scope-label');
    const folderSelect = document.getElementById('scope-folder');
    const fromInput = document.getElementById('scope-from');
    const toInput = document.getElementById('scope-to');
    const applyButton = document.getElementById('scope-apply');
    const clearButton = document.getElementById('scope-clear');
    if (!toggle || !popover) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    let scope = {};
    let foldersLoaded = false;

    try {
        scope = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}') || {};
    } catch (err) {
        scope = {};
    }

    function folderName(folderId) {
        const option = Array.from(folderSelect.options).find((item) => item.value === folderId);
        return option ? option.textContent : '';
    }

    function renderLabel() {
        const parts = [];
        if (scope.filter_folder !== undefined) {
            parts.push('📁 ' + (folderName(scope.filter_folder) || t('Unfiled')));
        }
        if (scope.filter_from || scope.filter_to) {
            parts.push('📅 ' + (scope.filter_from || '…') + ' – ' + (scope.filter_to || '…'));
        }
        label.textContent = parts.length ? parts.join(' · ') : t('All documents');
        toggle.classList.toggle('is-active', parts.length > 0);
    }

    function persist() {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(scope));
        renderLabel();
    }

    function loadFolders() {
        if (foldersLoaded) return;
        foldersLoaded = true;
        fetch('/api/documents', { credentials: 'same-origin' })
            .then((response) => response.json())
            .then((payload) => {
                (payload.folders || []).forEach((folder) => {
                    if (Array.from(folderSelect.options).some((item) => item.value === folder.id)) return;
                    const option = document.createElement('option');
                    option.value = folder.id;
                    option.textContent = folder.name;
                    folderSelect.appendChild(option);
                });
                renderLabel();
            })
            .catch(() => {});
    }

    toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        popover.hidden = !popover.hidden;
        if (!popover.hidden) {
            loadFolders();
            folderSelect.value = scope.filter_folder === undefined ? '' : scope.filter_folder;
            fromInput.value = scope.filter_from || '';
            toInput.value = scope.filter_to || '';
        }
    });

    document.addEventListener('click', (event) => {
        if (!popover.hidden && !popover.contains(event.target) && event.target !== toggle) {
            popover.hidden = true;
        }
    });

    applyButton.addEventListener('click', () => {
        scope = {};
        if (folderSelect.value !== '') scope.filter_folder = folderSelect.value;
        if (fromInput.value) scope.filter_from = fromInput.value;
        if (toInput.value) scope.filter_to = toInput.value;
        persist();
        popover.hidden = true;
    });

    clearButton.addEventListener('click', () => {
        scope = {};
        folderSelect.value = '';
        fromInput.value = '';
        toInput.value = '';
        persist();
        popover.hidden = true;
    });

    // The send flow appends whatever this returns to the request.
    window.getChatScope = () => Object.assign({}, scope);

    // Keep the label honest when a folder was removed in another tab.
    renderLabel();
    loadFolders();
})();
