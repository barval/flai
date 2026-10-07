// app/static/js/chat-documents.js
// Document management functions with index status, processing time display,
// periodic updates, and blinking animation for indexing documents.

let currentView = 'sessions'; // 'sessions' or 'documents'
let documentsData = {};
let foldersData = {}; // folder_id → folder (from GET /api/documents)
let documentTimerInterval = null;
let documentQueuePositions = {};
let selectedDocIds = new Set(); // documents checked for bulk operations
let activeFolderPicker = null; // open folder picker menu element
let draggedDocId = null;

// Documents selected for the RLM "Deep analysis" toggle. Clicking a
// document in the sidebar list toggles it; the choice is mirrored into
// the hidden #rlm-docs multi-select (source of truth for sendRlmAnalysis).
let rlmSelectedDocs = new Set();

// Apply the current view to the UI: update tab active state and show/hide the correct list
function applyCurrentView() {
    const view = currentView;
    // Update tab styling
    document.querySelectorAll('.header-tab').forEach(tab => {
        tab.classList.remove('active');
        if (tab.dataset.view === view) {
            tab.classList.add('active');
        }
    });
    // Show/hide lists
    const sessionsList = document.getElementById('sessions-list');
    const documentsList = document.getElementById('documents-list');
    if (view === 'sessions') {
        sessionsList.classList.add('active');
        sessionsList.style.display = 'block';
        documentsList.classList.remove('active');
        documentsList.classList.add('hidden');
    } else {
        sessionsList.classList.remove('active');
        sessionsList.style.display = 'none';
        documentsList.classList.remove('hidden');
        documentsList.classList.add('active');
        loadDocuments(); // immediate load (no polling — SSE handles updates)
    }
}

function switchView(view) {
    if (view === currentView) {
        // On mobile, toggle collapse when clicking active tab
        if (window.innerWidth <= 768) {
            const sidebar = document.querySelector('.sessions-sidebar');
            if (sidebar) {
                sidebar.classList.toggle('collapsed');
                const login = window.CURRENT_USER_LOGIN;
                if (login) {
                    localStorage.setItem(`sidebar_collapsed_${login}`, sidebar.classList.contains('collapsed'));
                }
            }
        }
        return;
    }

    currentView = view;
    applyCurrentView();

    // Save preference
    const login = window.CURRENT_USER_LOGIN;
    if (login) {
        localStorage.setItem(`current_view_${login}`, view);
    }
}

function loadDocuments(showLoading = true) {
    if (showLoading) {
        // Optional: show a loading indicator
    }
    fetch('/api/documents')
        .then(res => {
            if (!res.ok) {
                throw new Error(`HTTP error ${res.status}`);
            }
            return res.json();
        })
        .then(payload => {
            documentsData = {};
            (payload.documents || []).forEach(doc => {
                documentsData[doc.id] = doc;
            });
            updateDocumentsList(payload.documents || [], payload.folders || []);
            updateDocumentProcessingTimers();
            if (typeof fetchQueueStatus === 'function') fetchQueueStatus();
        })
        .catch(err => {
            console.error('Error loading documents:', err);
        });
}

function updateDocumentProcessingTimers() {
    const timers = document.querySelectorAll('.doc-live-timer[data-index-status="indexing"]');
    if (timers.length === 0) {
        if (documentTimerInterval) {
            clearInterval(documentTimerInterval);
            documentTimerInterval = null;
        }
        return;
    }
    timers.forEach(timer => {
        const elapsed = Number(timer.dataset.processingSeconds || 0) +
            Math.floor((performance.now() - Number(timer.dataset.timerStartedAt || performance.now())) / 1000);
        const seconds = Math.max(0, Math.round(elapsed));
        timer.textContent = ` ⏱️ ${seconds}${t('seconds_suffix')}`;
    });
}

function updateDocumentQueueStatus(queueStatus) {
    documentQueuePositions = {};
    (queueStatus.queued || []).forEach(item => {
        const position = Number(item.position_info?.position || 0);
        if (item.doc_id && position > 0) {
            const current = documentQueuePositions[item.doc_id];
            documentQueuePositions[item.doc_id] = current ? Math.min(current, position) : position;
        }
    });

    document.querySelectorAll('.document-item[data-index-status="pending"]').forEach(item => {
        const statusIcon = item.querySelector('.document-status-icon');
        if (!statusIcon) return;
        const position = documentQueuePositions[item.dataset.documentId] || 0;
        statusIcon.textContent = position > 0 ? `⏳ ${position}` : '⏳';
        statusIcon.classList.toggle('queued', position > 0);
        statusIcon.classList.toggle('blink', position > 0);
        statusIcon.title = t('status_pending') + (position > 0 ? ` (#${position})` : '');
    });
}

function getStatusIcon(status) {
    switch (status) {
        case 'pending':
            return '⏳'; // pending
        case 'indexing':
            return '⚡'; // indexing
        case 'indexed':
            return '✅'; // indexed
        case 'failed':
            return '❌'; // failed
        default:
            return '📄'; // unknown
    }
}

function getStatusTitle(status) {
    // Use translated strings from window.TRANSLATIONS
    switch (status) {
        case 'pending':
            return t('status_pending');
        case 'indexing':
            return t('status_indexing');
        case 'indexed':
            return t('status_indexed');
        case 'failed':
            return t('status_failed');
        default:
            return t('status_unknown');
    }
}

function renderDocItem(doc) {
    // Use indexed_at if available, otherwise uploaded_at
    const dateStr = doc.indexed_at ? formatFullDateTime(doc.indexed_at) : (doc.uploaded_at ? formatFullDateTime(doc.uploaded_at) : '');
    const fileSizeFormatted = doc.file_size ? formatFileSize(doc.file_size) : '';
    const statusIcon = getStatusIcon(doc.index_status);
    const statusTitle = getStatusTitle(doc.index_status);
    const isIndexing = doc.index_status === 'indexing';
    const isPending = doc.index_status === 'pending';
    const queuePosition = isPending ? documentQueuePositions[doc.id] || 0 : 0;
    const existingTimer = document.querySelector(
        `.doc-live-timer[data-document-id="${doc.id}"][data-index-status="indexing"]`
    );
    // Add blink class if indexing for the main status icon
    const iconClass = isIndexing
        ? 'document-status-icon blink'
        : queuePosition > 0
            ? 'document-status-icon queued blink'
            : 'document-status-icon';

    // Show a live elapsed timer while indexing and the server duration when complete.
    let statusIndicator = '';
    let indexingStartTimestamp = '';
    if (isIndexing) {
        const processingSeconds = Math.round(doc.processing_time || 0);
        statusIndicator = ` ⏱️ ${processingSeconds}${t('seconds_suffix')}`;
        const timerStartedAt = existingTimer?.dataset.timerStartedAt || performance.now();
        indexingStartTimestamp = ` data-document-id="${doc.id}" data-index-status="indexing" data-processing-seconds="${processingSeconds}" data-timer-started-at="${timerStartedAt}"`;
    } else if (isPending) {
        indexingStartTimestamp = ` data-document-id="${doc.id}" data-index-status="pending"`;
    } else if (doc.index_status) {
        indexingStartTimestamp = ` data-document-id="${doc.id}" data-index-status="${escapeHtml(doc.index_status)}"`;
    }

    // Show processing_time for completed documents
    let processingTimeStr = '';
    if (doc.processing_time !== null && doc.processing_time !== undefined) {
        const processingSeconds = Math.round(doc.processing_time);
        processingTimeStr = ` ⏱️ ${processingSeconds}${t('seconds_suffix')}`;
    }

    // Embedding model is the search index model; only show it after indexing.
    let displayModel = doc.embedding_model || '';
    let embeddingLine = '';
    if (displayModel) {
        embeddingLine = `<div class="document-embedding"><span class="document-status-icon">🔄</span> ${escapeHtml(displayModel)}</div>`;
    }

    // Recognition model is the multimodal model used to read image content.
    let descriptionLine = '';
    if (doc.description_model) {
        descriptionLine = `<div class="document-description-model"><span class="document-status-icon">🖼️</span> ${escapeHtml(doc.description_model)}</div>`;
    }

    const isRlmSelected = rlmSelectedDocs.has(doc.id);
    const docChecked = selectedDocIds.has(doc.id);

    return `
    <div class="document-item${isRlmSelected ? ' rlm-selected' : ''}" data-document-id="${doc.id}" data-document-name="${escapeHtml(doc.filename)}" data-index-status="${escapeHtml(doc.index_status || '')}" data-folder-id="${escapeHtml(doc.folder_id || '')}" draggable="true">
        <div class="document-content">
            <label class="document-checkbox" title="${isRlmMode() ? t('documents_select_for_analysis') : t('documents_select_for_move')}">
                <input type="checkbox" class="doc-check" data-document-id="${doc.id}"${docChecked ? ' checked' : ''}>
            </label>
            <div class="document-info">
                <div class="document-title">
                    <span class="${iconClass}" title="${statusTitle}${queuePosition > 0 ? ` (#${queuePosition})` : ''}">${isPending && queuePosition > 0 ? `⏳ ${queuePosition}` : statusIcon}</span>
                    📄 ${escapeHtml(doc.filename)}<span class="rlm-marker">${isRlmSelected ? ' ✓' : ''}</span>
                </div>
                <div class="document-date">📅 ${dateStr} ${fileSizeFormatted ? '[' + fileSizeFormatted + ']' : ''}<span class="doc-live-timer"${indexingStartTimestamp}>${statusIndicator || processingTimeStr}</span></div>
                ${embeddingLine}
                ${descriptionLine}
            </div>
            <div class="document-actions">
                <button class="move-document-button" title="${t('folder_move')}">➤ 📂</button>
                <button class="delete-document-button" title="${t('delete_document')}">🗑️</button>
            </div>
        </div>
    </div>
    `;
}

function renderDocItems(docs) {
    return docs.map(doc => renderDocItem(doc)).join('');
}

function isFolderCollapsed(folderId) {
    const login = window.CURRENT_USER_LOGIN || '';
    return localStorage.getItem(`docfolder_collapsed_${login}_${folderId}`) === '1';
}

function setFolderCollapsed(folderId, collapsed) {
    const login = window.CURRENT_USER_LOGIN || '';
    localStorage.setItem(`docfolder_collapsed_${login}_${folderId}`, collapsed ? '1' : '0');
}

function renderFolderItem(folder) {
    const collapsed = isFolderCollapsed(folder.id);
    const docs = Object.values(documentsData).filter(d => d.folder_id === folder.id);
    const meta = folder.count > 0 ? `${folder.count} · ${formatFileSize(folder.size)}` : '';
    return `
    <div class="document-folder" data-folder-id="${folder.id}">
        <div class="document-folder-header${collapsed ? ' collapsed' : ''}" data-folder-id="${folder.id}">
            <label class="document-checkbox" title="${t('documents_select_folder')}">
                <input type="checkbox" class="folder-check" data-folder-id="${folder.id}">
            </label>
            <span class="folder-chevron">${collapsed ? '📁' : '📂'}</span>
            <span class="folder-name">${escapeHtml(folder.name)}</span>
            <span class="folder-meta">${meta}</span>
            <button class="rename-folder-button" title="${t('folder_rename')}">✏️</button>
            <button class="delete-folder-button" title="${t('folder_delete')}">🗑️</button>
        </div>
        <div class="document-folder-docs${collapsed ? ' hidden' : ''}">
            ${renderDocItems(docs)}
        </div>
    </div>
    `;
}

function updateDocumentsList(documents, folders = []) {
    const documentsList = document.getElementById('documents-list');
    const documentsCount = document.getElementById('documents-count');

    foldersData = {};
    folders.forEach(folder => {
        foldersData[folder.id] = folder;
    });

    // Drop RLM selections pointing to documents that no longer exist
    // (e.g. deleted while selected).
    const liveIds = new Set(documents.map(d => d.id));
    for (const id of rlmSelectedDocs) {
        if (!liveIds.has(id)) rlmSelectedDocs.delete(id);
    }
    // Same for bulk selections.
    for (const id of selectedDocIds) {
        if (!liveIds.has(id)) selectedDocIds.delete(id);
    }

    documents.sort((a, b) => new Date(b.uploaded_at) - new Date(a.uploaded_at));

    const folderDocs = folders.map(folder => renderFolderItem(folder)).join('');
    const rootDocs = documents.filter(d => !d.folder_id);

    let html = `
    <div class="bulk-documents-bar hidden" id="bulk-documents-bar">
        <span id="bulk-selected-count" class="bulk-label"></span>
        <button id="bulk-move-button" class="bulk-move-button" title="${t('folder_move')}">➤ 📂</button>
        <button id="bulk-clear-button" class="bulk-clear-button" title="${t('folder_clear_selection')}">✖</button>
    </div>
    `;
    html += folderDocs;
    if (rootDocs.length > 0) {
        html += `<div class="document-folder-docs folder-root">${renderDocItems(rootDocs)}</div>`;
    }

    documentsList.innerHTML = html;
    if (documentTimerInterval) clearInterval(documentTimerInterval);
    if (documents.some(doc => doc.index_status === 'indexing')) {
        documentTimerInterval = setInterval(updateDocumentProcessingTimers, 1000);
    } else {
        documentTimerInterval = null;
    }
    if (documentsCount) {
        documentsCount.textContent = documents.length;
    }

    // Mirror the document list into the RLM deep-analysis picker (if present)
    const rlmDocs = document.getElementById('rlm-docs');
    if (rlmDocs) {
        rlmDocs.innerHTML = '';
        documents.forEach(doc => {
            const opt = document.createElement('option');
            opt.value = doc.id;
            opt.textContent = doc.filename;
            rlmDocs.appendChild(opt);
        });
        // Restore selection after the rebuild
        syncRlmDocsSelect();
    }

    attachDocumentEventHandlers();
    updateRlmToggleCount();
    updateBulkBar();
    updateFolderCheckboxes();
}

// Sync the hidden #rlm-docs multi-select (used by sendRlmAnalysis) with the
// documents currently marked for deep analysis.
function syncRlmDocsSelect() {
    const rlmDocs = document.getElementById('rlm-docs');
    if (!rlmDocs) return;
    for (const opt of rlmDocs.options) {
        opt.selected = rlmSelectedDocs.has(opt.value);
    }
}

// Show the count of documents selected for the RLM toggle, e.g. "Deep analysis (2)".
function updateRlmToggleCount() {
    const countEl = document.getElementById('rlm-count');
    if (!countEl) return;
    const n = rlmSelectedDocs.size;
    countEl.textContent = n > 0 ? ` (${n})` : '';
}

function attachDocumentEventHandlers() {
    document.querySelectorAll('.delete-document-button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const docItem = this.closest('.document-item');
            const docId = docItem.dataset.documentId;
            const docName = docItem.dataset.documentName;
            deleteDocument(docId, docName);
        });
    });
    document.querySelectorAll('.move-document-button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const docId = this.closest('.document-item').dataset.documentId;
            showFolderPicker(this, function(folderId) {
                setDocFolder(docId, folderId);
            });
        });
    });
    // Checkboxes are the single document picker. With the "Deep analysis"
    // toggle on they drive the RLM selection (setDocChecked mirrors it).
    document.querySelectorAll('.doc-check').forEach(cb => {
        cb.addEventListener('change', function(e) {
            e.stopPropagation();
            setDocChecked(this.dataset.documentId, this.checked);
            updateBulkBar();
            updateFolderCheckboxes();
        });
    });
    // Checkboxes on folders pick every document in the folder (tristate).
    document.querySelectorAll('.folder-check').forEach(cb => {
        cb.addEventListener('change', function(e) {
            e.stopPropagation();
            const folderId = this.dataset.folderId;
            const ids = folderDocIds(folderId);
            ids.forEach(id => setDocChecked(id, this.checked));
            document.querySelectorAll(`.doc-check[data-document-id]`).forEach(docCb => {
                if (ids.includes(docCb.dataset.documentId)) {
                    docCb.checked = this.checked;
                }
            });
            updateBulkBar();
            updateFolderCheckboxes();
        });
    });
    // Bulk operations bar.
    const bulkMoveBtn = document.getElementById('bulk-move-button');
    if (bulkMoveBtn) {
        bulkMoveBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            showFolderPicker(this, function(folderId) {
                moveSelectedDocuments(folderId);
            });
        });
    }
    const bulkClearBtn = document.getElementById('bulk-clear-button');
    if (bulkClearBtn) {
        bulkClearBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            clearDocumentSelection();
        });
    }
    // Folders: collapse toggle, rename, delete.
    document.querySelectorAll('.document-folder-header').forEach(header => {
        header.addEventListener('click', function(e) {
            if (e.target.closest('button, .document-checkbox, .folder-check')) return;
            toggleFolderCollapse(this.dataset.folderId);
        });
    });
    document.querySelectorAll('.rename-folder-button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            renameFolder(this.closest('.document-folder-header').dataset.folderId);
        });
    });
    document.querySelectorAll('.delete-folder-button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            deleteFolder(this.closest('.document-folder-header').dataset.folderId);
        });
    });
    attachDocumentDragAndDrop();
}

function folderDocIds(folderId) {
    return Object.values(documentsData)
        .filter(doc => (doc.folder_id || '') === folderId)
        .map(doc => doc.id);
}

function updateFolderCheckboxes() {
    document.querySelectorAll('.folder-check').forEach(cb => {
        const id = folderDocIds(cb.dataset.folderId);
        const selectedCount = id.filter(docId => selectedDocIds.has(docId)).length;
        cb.checked = id.length > 0 && selectedCount === id.length;
        cb.indeterminate = selectedCount > 0 && selectedCount < id.length;
    });
}

function updateBulkBar() {
    const bar = document.getElementById('bulk-documents-bar');
    if (!bar) return;
    const n = selectedDocIds.size;
    bar.classList.toggle('hidden', n === 0);
    // Hiding the bar also drops the stale checkbox refs below, so always
    // recompute the total size from the live selection.
    let totalSize = 0;
    selectedDocIds.forEach(id => {
        const doc = documentsData[id];
        if (doc && doc.file_size) totalSize += doc.file_size;
    });
    const label = document.getElementById('bulk-selected-count');
    if (label) {
        label.textContent = formatString(t('documents_selected_count'), { count: n, size: formatFileSize(totalSize) });
    }
    // Moving makes no sense while the checkboxes drive the analysis selection.
    const moveBtn = document.getElementById('bulk-move-button');
    if (moveBtn) moveBtn.classList.toggle('hidden', isRlmMode());
}

function clearDocumentSelection() {
    selectedDocIds = new Set();
    // In RLM mode the analysis selection is driven by the same checkboxes.
    if (isRlmMode()) {
        rlmSelectedDocs = new Set();
        applyRlmMarkers();
        updateRlmToggleCount();
        syncRlmDocsSelect();
    }
    document.querySelectorAll('.doc-check').forEach(cb => {
        cb.checked = false;
    });
    document.querySelectorAll('.folder-check').forEach(cb => {
        cb.checked = false;
        cb.indeterminate = false;
    });
    updateBulkBar();
}

function toggleFolderCollapse(folderId) {
    const folderEl = document.querySelector(`.document-folder[data-folder-id="${folderId}"]`);
    if (!folderEl) return;
    const collapsed = !folderEl.querySelector('.document-folder-docs').classList.contains('hidden');
    const header = folderEl.querySelector('.document-folder-header');
    const docsWrap = folderEl.querySelector('.document-folder-docs');
    header.classList.toggle('collapsed', collapsed);
    docsWrap.classList.toggle('hidden', collapsed);
    header.querySelector('.folder-chevron').textContent = collapsed ? '📁' : '📂';
    setFolderCollapsed(folderId, collapsed);
}

let folderNameDialogTarget = null; // null → create; folder_id → rename

function showFolderNameError(message) {
    const err = document.getElementById('folder-name-error');
    if (!err) return;
    if (message) {
        err.textContent = message;
        err.hidden = false;
    } else {
        err.hidden = true;
        err.textContent = '';
    }
}

function openFolderNameDialog(mode, folderId) {
    const modal = document.getElementById('folder-name-modal');
    if (!modal) return;
    folderNameDialogTarget = mode === 'rename' ? folderId : null;
    const title = document.getElementById('folder-name-title');
    const submit = document.getElementById('folder-name-submit');
    const input = document.getElementById('folder-name-input');
    if (!title || !submit || !input) return;
    if (mode === 'rename') {
        const folder = foldersData[folderId];
        if (!folder) return;
        title.textContent = t('folder_rename_title');
        submit.textContent = t('save');
        input.value = folder.name;
    } else {
        title.textContent = t('folder_create_title');
        submit.textContent = t('create');
        input.value = '';
    }
    showFolderNameError(null);
    modal.hidden = false;
    input.focus();
    input.select();
    document.addEventListener('keydown', onFolderNameDialogKeydown);
}

function closeFolderNameDialog() {
    const modal = document.getElementById('folder-name-modal');
    if (!modal) return;
    modal.hidden = true;
    folderNameDialogTarget = null;
    document.removeEventListener('keydown', onFolderNameDialogKeydown);
}

function onFolderNameDialogKeydown(e) {
    if (e.key === 'Escape') {
        closeFolderNameDialog();
    } else if (e.key === 'Enter') {
        submitFolderNameDialog();
    }
}

function submitFolderNameDialog() {
    const input = document.getElementById('folder-name-input');
    if (!input) return;
    const name = (input.value || '').trim();
    if (!name) {
        showFolderNameError(t('folder_name_label'));
        return;
    }
    if (folderNameDialogTarget === null) {
        createFolderWithName(name);
    } else {
        const folder = foldersData[folderNameDialogTarget];
        if (folder && name === folder.name) {
            closeFolderNameDialog();
            return;
        }
        renameFolderWithName(folderNameDialogTarget, name);
    }
}

function createFolderWithName(name) {
    fetchWithCSRF('/api/document-folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                closeFolderNameDialog();
                loadDocuments();
            } else {
                showFolderNameError(data.error || t('unknown_error'));
            }
        })
        .catch(err => showFolderNameError(err.message));
}

function renameFolderWithName(folderId, name) {
    fetchWithCSRF(`/api/document-folders/${folderId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                closeFolderNameDialog();
                loadDocuments();
            } else {
                showFolderNameError(data.error || t('unknown_error'));
            }
        })
        .catch(err => showFolderNameError(err.message));
}

function createFolder() {
    openFolderNameDialog('create');
}

function renameFolder(folderId) {
    openFolderNameDialog('rename', folderId);
}

function deleteFolder(folderId) {
    const folder = foldersData[folderId];
    if (!folder) return;
    if (!confirm(formatString(t('folder_delete_confirm'), { name: folder.name }))) return;
    if (folder.count > 0) {
        const sizeText = formatFileSize(folder.size || 0);
        if (!confirm(formatString(t('folder_delete_cascade_confirm'), { count: folder.count, size: sizeText }))) return;
    }
    fetchWithCSRF(`/api/document-folders/${folderId}`, { method: 'DELETE' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                loadDocuments();
            } else {
                alert(t('error') + ': ' + (data.error || t('unknown_error')));
            }
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

function setDocFolder(docId, folderId) {
    fetchWithCSRF(`/api/documents/${docId}/folder`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: folderId })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                loadDocuments();
            } else {
                alert(t('error') + ': ' + (data.error || t('unknown_error')));
            }
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

function moveSelectedDocuments(folderId) {
    const docIds = Array.from(selectedDocIds);
    if (docIds.length === 0) return;
    fetchWithCSRF('/api/documents/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_ids: docIds, folder_id: folderId })
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                clearDocumentSelection();
                loadDocuments();
            } else {
                alert(t('error') + ': ' + (data.error || t('unknown_error')));
            }
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

function closeFolderPicker() {
    if (activeFolderPicker) {
        activeFolderPicker.remove();
        activeFolderPicker = null;
    }
}

function showFolderPicker(anchor, onPick) {
    closeFolderPicker();
    const picker = document.createElement('div');
    picker.className = 'folder-picker-menu';
    picker.dataset.testid = 'folder-picker';

    const rootBtn = document.createElement('button');
    rootBtn.type = 'button';
    rootBtn.className = 'folder-picker-item folder-picker-root';
    rootBtn.textContent = t('folder_move_to_root');
    rootBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        closeFolderPicker();
        onPick(null);
    });
    picker.appendChild(rootBtn);

    Object.values(foldersData).forEach(folder => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'folder-picker-item folder-picker-folder';
        btn.textContent = `📁 ${folder.name}`;
        btn.dataset.folderId = folder.id;
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            closeFolderPicker();
            onPick(folder.id);
        });
        picker.appendChild(btn);
    });

    document.body.appendChild(picker);
    activeFolderPicker = picker;

    const rect = anchor.getBoundingClientRect();
    picker.style.top = Math.min(rect.bottom + 4, window.innerHeight - picker.offsetHeight - 8) + 'px';
    picker.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - picker.offsetWidth - 8)) + 'px';

    const onDocumentClick = function(e) {
        if (!picker.contains(e.target)) closeFolderPicker();
        document.removeEventListener('click', onDocumentClick);
    };
    const onKeyDown = function(e) {
        if (e.key === 'Escape') closeFolderPicker();
        document.removeEventListener('keydown', onKeyDown);
    };
    setTimeout(() => document.addEventListener('click', onDocumentClick), 0);
    document.addEventListener('keydown', onKeyDown);
}

function attachDocumentDragAndDrop() {
    document.removeEventListener('dragstart', onDocumentDragStart);
    document.removeEventListener('dragend', onDocumentDragEnd);
    document.removeEventListener('dragover', onDocumentDragOver);
    document.removeEventListener('drop', onDocumentDrop);
    document.addEventListener('dragstart', onDocumentDragStart);
    document.addEventListener('dragend', onDocumentDragEnd);
    document.addEventListener('dragover', onDocumentDragOver);
    document.addEventListener('drop', onDocumentDrop);
}

function onDocumentDragStart(e) {
    const item = e.target.closest('.document-item');
    if (!item) return;
    draggedDocId = item.dataset.documentId;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', draggedDocId);
}

function onDocumentDragEnd() {
    draggedDocId = null;
    document.querySelectorAll('.document-folder-header.drag-over').forEach(el => el.classList.remove('drag-over'));
}

function onDocumentDragOver(e) {
    if (!draggedDocId) return;
    const header = e.target.closest('.document-folder-header');
    if (!header) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    header.classList.add('drag-over');
}

function onDocumentDrop(e) {
    if (!e.target.closest('.document-folder-header')) return;
    const header = e.target.closest('.document-folder-header');
    header.classList.remove('drag-over');
    e.preventDefault();
    if (draggedDocId) {
        setDocFolder(draggedDocId, header.dataset.folderId);
    }
}

// True while the "Deep analysis" toggle is on: checkboxes then drive the
// RLM selection instead of the document-move selection.
function isRlmMode() {
    const toggle = document.getElementById('rlm-toggle');
    return !!(toggle && toggle.checked);
}

// Mark/unmark every rendered document according to the RLM selection set.
function applyRlmMarkers() {
    document.querySelectorAll('.document-item').forEach(item => {
        const id = item.dataset.documentId;
        const selected = rlmSelectedDocs.has(id);
        item.classList.toggle('rlm-selected', selected);
        const marker = item.querySelector('.rlm-marker');
        if (marker) marker.textContent = selected ? ' ✓' : '';
    });
}

// Pick a document with the single checkbox set. In RLM mode the pick is
// mirrored into the deep-analysis selection; otherwise it feeds the move bar.
function setDocChecked(docId, checked) {
    if (checked) {
        selectedDocIds.add(docId);
    } else {
        selectedDocIds.delete(docId);
    }
    if (isRlmMode()) {
        if (checked) {
            rlmSelectedDocs.add(docId);
        } else {
            rlmSelectedDocs.delete(docId);
        }
        applyRlmMarkers();
        updateRlmToggleCount();
        syncRlmDocsSelect();
    }
}

// When the toggle is switched on, the current checkbox selection becomes the
// deep-analysis corpus (previously-selected RLM docs are not dropped).
function syncRlmFromSelection() {
    rlmSelectedDocs = new Set(selectedDocIds);
    applyRlmMarkers();
    updateRlmToggleCount();
    syncRlmDocsSelect();
}

function deleteDocument(docId, docName) {
    const confirmMessage = formatString(t('delete_document_confirm'), {
        filename: docName
    });
    if (!confirm(confirmMessage)) return;

    fetchWithCSRF(`/api/documents/${docId}`, { method: 'DELETE' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                delete documentsData[docId];
                const docItem = document.querySelector(`.document-item[data-document-id="${docId}"]`);
                if (docItem) docItem.remove();
                const documentsCount = document.querySelectorAll('.document-item').length;
                document.getElementById('documents-count').textContent = documentsCount;
            } else {
                alert(t('error') + ': ' + (data.error || t('unknown_error')));
            }
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

function uploadDocument(file, folderId) {
    const formData = new FormData();
    formData.append('file', file);
    if (folderId) {
        formData.append('folder_id', folderId);
    }

    fetchWithCSRF('/api/documents/upload', {
        method: 'POST',
        body: formData
    })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                loadDocuments(); // Reload list to show new document with pending status
            } else {
                alert(t('error') + ': ' + (data.error || t('document_upload_failed')));
            }
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

// Open the file picker and upload into the chosen folder (null = root).
function openFilePickerForFolder(folderId) {
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.pdf,.doc,.docx,.txt,.odt,.rtf,.csv,.json,.epub';
    fileInput.onchange = function(e) {
        if (e.target.files.length > 0) {
            uploadDocument(e.target.files[0], folderId);
        }
    };
    fileInput.click();
}

function initDocumentsView() {
    // Load saved view preference
    const login = window.CURRENT_USER_LOGIN;
    if (login) {
        const savedView = localStorage.getItem(`current_view_${login}`);
        if (savedView && ['sessions', 'documents'].includes(savedView)) {
            currentView = savedView;
        }
    }

    // Set up tab click handlers
    document.querySelectorAll('.header-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            switchView(this.dataset.view);
        });
    });

    // Set up new document button (opens the folder-target menu when folders exist)
    const newDocBtn = document.getElementById('new-document-button');
    if (newDocBtn) {
        newDocBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (Object.keys(foldersData).length === 0) {
                openFilePickerForFolder(null);
                return;
            }
            showFolderPicker(e.currentTarget, function(folderId) {
                openFilePickerForFolder(folderId);
            });
        });
    }

    // Set up new folder button
    const addFolderBtn = document.getElementById('add-folder-button');
    if (addFolderBtn) {
        addFolderBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            createFolder();
        });
    }

    // Folder name dialog wiring (create/rename)
    const folderNameModal = document.getElementById('folder-name-modal');
    if (folderNameModal) {
        folderNameModal.querySelectorAll('[data-folder-name-close]').forEach(el => {
            el.addEventListener('click', closeFolderNameDialog);
        });
        const cancelBtn = document.getElementById('folder-name-cancel');
        if (cancelBtn) {
            cancelBtn.textContent = t('cancel');
            cancelBtn.addEventListener('click', closeFolderNameDialog);
        }
        const submitBtn = document.getElementById('folder-name-submit');
        if (submitBtn) {
            submitBtn.addEventListener('click', submitFolderNameDialog);
        }
    }

    // "Deep analysis" toggle: switching it on turns the current checkbox
    // selection into the RLM corpus; switching it off returns the checkboxes
    // to move selection without dropping the analysis selection.
    const rlmToggleEl = document.getElementById('rlm-toggle');
    if (rlmToggleEl) {
        rlmToggleEl.addEventListener('change', function() {
            if (rlmToggleEl.checked) {
                syncRlmFromSelection();
            } else {
                updateBulkBar();
            }
        });
    }

    // Select / clear every document with the semantics of the current mode.
    const selectAllBtn = document.getElementById('doc-select-all-button');
    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            const allIds = Object.keys(documentsData);
            allIds.forEach(id => selectedDocIds.add(id));
            if (isRlmMode()) syncRlmFromSelection();
            updateDocumentsList(Object.values(documentsData), Object.values(foldersData));
        });
    }
    const selectNoneBtn = document.getElementById('doc-select-none-button');
    if (selectNoneBtn) {
        selectNoneBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            selectedDocIds = new Set();
            if (isRlmMode()) {
                rlmSelectedDocs = new Set();
                updateRlmToggleCount();
            }
            updateDocumentsList(Object.values(documentsData), Object.values(foldersData));
        });
    }

    // Apply the initial view (synchronizes UI with currentView)
    applyCurrentView();
}
