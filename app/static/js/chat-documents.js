// app/static/js/chat-documents.js
// Document management functions with index status, processing time display,
// periodic updates, and blinking animation for indexing documents.

// The virtual group for documents that live in the root of the Documents tab.
// It behaves like a folder: the group checkbox selects all root documents and
// the search scope can pick it together with any folders.
const ROOT_GROUP = '__root__';

let currentView = 'sessions'; // 'sessions' or 'documents'
let documentsData = {};
let foldersData = {}; // folder_id → folder (from GET /api/documents)
let documentTimerInterval = null;
let documentQueuePositions = {};
let selectedDocIds = new Set(); // documents checked for bulk operations
let activeFolderPicker = null; // open folder picker menu element
let draggedDocId = null;
let previewDocId = null; // document open in the main-window preview
let previewDocName = '';

// Documents selected for the RLM "Deep analysis" toggle. Clicking a
// document in the sidebar list toggles it; the choice is mirrored into
// the hidden #rlm-docs multi-select (source of truth for sendRlmAnalysis).
let rlmSelectedDocs = new Set();
// The folder groups the current corpus came from (the scope's "Apply"). Kept
// separately from the ticks so the chip can say "folders (2)" even after a new
// document is uploaded into one of those folders: the corpus is a snapshot.
let corpusGroupNames = [];
let applyingCorpusFilter = false;

// Apply the current view to the UI: update tab active state and show/hide the correct list
function applyCurrentView() {
    const view = currentView;
    // The preview lives in the main window and belongs to the Documents tab:
    // any other tab (or a task/session) restores the chat/task window.
    if (view !== 'documents') closeDocumentPreview();
    // The Tasks tab owns the main window too: the empty task state when no
    // task is selected (chat-tasks.js), the task conversation otherwise.
    // Only the Sessions tab restores the chat conversation — the Documents
    // tab is a sidebar view, so the main window keeps whatever was open
    // (an active task stays open, a chat stays the chat) until a document
    // is clicked.
    if (typeof enterTasksEmptyState === 'function' && typeof leaveTasksView === 'function') {
        if (view === 'tasks') {
            if (!window.currentTaskId) {
                // Reopen the last task (tab switch or page reload); the empty
                // state is the fallback when there is nothing to restore.
                if (typeof restoreTasksView === 'function') restoreTasksView();
                else enterTasksEmptyState();
            }
        } else if (view === 'sessions' && typeof isTaskViewActive === 'function' && isTaskViewActive()) {
            leaveTasksView();
        }
    }
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
    const tasksList = document.getElementById('tasks-list');
    if (view === 'sessions') {
        sessionsList.classList.add('active');
        sessionsList.style.display = 'block';
        documentsList.classList.remove('active');
        documentsList.classList.add('hidden');
        if (tasksList) {
            tasksList.classList.remove('active');
            tasksList.classList.add('hidden');
        }
    } else if (view === 'documents') {
        sessionsList.classList.remove('active');
        sessionsList.style.display = 'none';
        documentsList.classList.remove('hidden');
        documentsList.classList.add('active');
        if (tasksList) {
            tasksList.classList.remove('active');
            tasksList.classList.add('hidden');
        }
        loadDocuments(); // immediate load (no polling — SSE handles updates)
    } else if (view === 'tasks') {
        sessionsList.classList.remove('active');
        sessionsList.style.display = 'none';
        documentsList.classList.remove('active');
        documentsList.classList.add('hidden');
        if (tasksList) {
            tasksList.classList.remove('hidden');
            tasksList.classList.add('active');
        }
        if (typeof loadTasksFromServer === 'function') {
            loadTasksFromServer(); // immediate load (no polling — SSE handles updates)
        }
    }
}

function switchView(view) {
    if (window.isMobileViewport && window.isMobileViewport()) {
        // Mobile: clicking a tab opens its list as a full-screen panel;
        // clicking the tab of the already-open panel returns to the chat.
        const sidebar = document.querySelector('.sessions-sidebar');
        const panelOpen = sidebar && sidebar.classList.contains('panel-open');
        const currentTabActive = document.querySelector(`.header-tab[data-view="${view}"]`);
        const isTabActive = currentTabActive && currentTabActive.classList.contains('active');
        if (panelOpen && isTabActive) {
            sidebar.classList.remove('panel-open');
            return;
        }
        if (sidebar) {
            sidebar.classList.add('panel-open');
        }
    }
    if (view === currentView) {
        // Desktop: clicking the active tab keeps the view unchanged.
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

    const showRlm = isRlmMode() && rlmSelectedDocs.has(doc.id);
    const docChecked = selectedDocIds.has(doc.id);

    return `
    <div class="document-item${showRlm ? ' rlm-selected' : ''}" data-document-id="${doc.id}" data-document-name="${escapeHtml(doc.filename)}" data-index-status="${escapeHtml(doc.index_status || '')}" data-folder-id="${escapeHtml(doc.folder_id || '')}" draggable="true">
        <div class="document-content">
            <label class="document-checkbox" title="${isRlmMode() ? t('documents_select_for_analysis') : t('documents_select_for_move')}">
                <input type="checkbox" class="doc-check" data-document-id="${doc.id}"${docChecked ? ' checked' : ''}>
            </label>
            <div class="document-info">
                <div class="document-title">
                    <span class="${iconClass}" title="${statusTitle}${queuePosition > 0 ? ` (#${queuePosition})` : ''}">${isPending && queuePosition > 0 ? `⏳ ${queuePosition}` : statusIcon}</span>
                    📄 ${escapeHtml(doc.filename)}<span class="rlm-marker">${showRlm ? ' ✓' : ''}</span>
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
        <button id="bulk-delete-button" class="bulk-delete-button" title="${t('delete_selected')}">🗑️</button>
    </div>
    `;
    html += folderDocs;
    if (rootDocs.length > 0) {
        // The root is a real selection group, not a leftover: the scope can pick
        // "Unfiled" (root) together with any folders, so it needs the same
        // group checkbox the folders have.
        html += `
        <div class="document-folder" data-folder-id="${ROOT_GROUP}">
            <div class="document-folder-header" data-folder-id="${ROOT_GROUP}">
                <label class="document-checkbox" title="${t('documents_select_folder')}">
                    <input type="checkbox" class="folder-check" data-folder-id="${ROOT_GROUP}">
                </label>
                <span class="folder-chevron">📂</span>
                <span class="folder-name">${t('Unfiled')}</span>
                <span class="folder-meta">${rootDocs.length}</span>
            </div>
            <div class="document-folder-docs">${renderDocItems(rootDocs)}</div>
        </div>`;
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
    if (typeof window.renderCorpusChip === 'function') window.renderCorpusChip();
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
// The count is visible only while the toggle is on.
function updateRlmToggleCount() {
    const countEl = document.getElementById('rlm-count');
    if (!countEl) return;
    const n = isRlmMode() ? rlmSelectedDocs.size : 0;
    countEl.textContent = n > 0 ? ` (${n})` : '';
}

function attachDocumentEventHandlers() {
    // A click on the document card (not on the checkbox or the action buttons)
    // opens the read-only preview in the main window.
    document.querySelectorAll('.document-item').forEach(item => {
        item.addEventListener('click', function(e) {
            if (e.target.closest('.document-checkbox, .document-actions, button, input, label')) return;
            openDocumentPreview(this.dataset.documentId, this.dataset.documentName || '');
        });
    });
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
    const bulkDeleteBtn = document.getElementById('bulk-delete-button');
    if (bulkDeleteBtn) {
        bulkDeleteBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            deleteSelectedDocuments();
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
        .filter(doc => (doc.folder_id || ROOT_GROUP) === folderId)
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
    // The analysis set mirrors the checkboxes in every mode, so it is cleared
    // unconditionally here; the marker pass hides any leftover tint.
    rlmSelectedDocs = new Set();
    corpusGroupNames = [];
    applyRlmMarkers();
    updateRlmToggleCount();
    syncRlmDocsSelect();
    document.querySelectorAll('.doc-check').forEach(cb => {
        cb.checked = false;
    });
    document.querySelectorAll('.folder-check').forEach(cb => {
        cb.checked = false;
        cb.indeterminate = false;
    });
    updateBulkBar();
    if (typeof window.renderCorpusChip === 'function') window.renderCorpusChip();
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

// ---------------------------------------------------------------------------
// Document preview (main window, Documents tab)
// ---------------------------------------------------------------------------

function setPreviewMode(active, name) {
    const chatTitle = document.getElementById('chat-title-text');
    const taskTitle = document.getElementById('task-title-text');
    const previewTitle = document.getElementById('preview-title-text');
    const contextInfo = document.getElementById('context-info');
    const statusCounter = document.getElementById('status-counter');
    const styleLabel = document.querySelector('.response-style-label');
    const styleDropdown = document.querySelector('.response-style-dropdown');
    const divider = document.querySelector('.header-divider');
    const saveChat = document.getElementById('save-chat-button');
    const saveDocument = document.getElementById('save-document-button');
    const stopTask = document.getElementById('stop-task-button');
    const retryTask = document.getElementById('retry-task-button');
    const cancelStream = document.getElementById('cancel-stream-header');
    const messages = document.getElementById('chat-messages');
    const preview = document.getElementById('document-preview');
    const rlmControls = document.querySelector('.rlm-controls');
    const inputArea = document.querySelector('.message-input');

    if (active) {
        document.body.classList.add('document-preview-mode');
        if (chatTitle) chatTitle.classList.add('hidden');
        if (taskTitle) taskTitle.classList.add('hidden');
        if (previewTitle) {
            previewTitle.classList.remove('hidden');
            previewTitle.textContent = formatString(t('document_preview_title'), { name });
            previewTitle.title = name;
        }
        if (contextInfo) contextInfo.style.display = 'none';
        if (statusCounter) statusCounter.style.display = 'none';
        if (styleLabel) styleLabel.style.display = 'none';
        if (styleDropdown) styleDropdown.style.display = 'none';
        if (divider) divider.style.display = 'none';
        if (saveChat) saveChat.style.display = 'none';
        if (saveDocument) saveDocument.style.display = 'inline-flex';
        if (stopTask) stopTask.style.display = 'none';
        if (retryTask) retryTask.style.display = 'none';
        if (cancelStream) cancelStream.style.display = 'none';
        if (messages) messages.classList.add('hidden');
        if (preview) preview.classList.remove('hidden');
        if (rlmControls) rlmControls.style.display = 'none';
        if (inputArea) inputArea.style.display = 'none';
    } else {
        document.body.classList.remove('document-preview-mode');
        if (chatTitle) chatTitle.classList.remove('hidden');
        if (taskTitle) taskTitle.classList.add('hidden');
        if (previewTitle) {
            previewTitle.classList.add('hidden');
            previewTitle.textContent = '';
            previewTitle.title = '';
        }
        if (contextInfo) contextInfo.style.display = '';
        if (statusCounter) statusCounter.style.display = '';
        if (styleLabel) styleLabel.style.display = '';
        if (styleDropdown) styleDropdown.style.display = '';
        if (divider) divider.style.display = '';
        if (saveChat) saveChat.style.display = '';
        if (saveDocument) saveDocument.style.display = 'none';
        if (stopTask) stopTask.style.display = '';
        if (retryTask) retryTask.style.display = 'none';
        if (cancelStream) cancelStream.style.display = '';
        if (messages) messages.classList.remove('hidden');
        if (preview) {
            preview.classList.add('hidden');
            preview.innerHTML = '';
        }
        if (rlmControls) rlmControls.style.display = '';
        if (inputArea) inputArea.style.display = '';
    }
}

function closeDocumentPreview() {
    if (!previewDocId) return;
    previewDocId = null;
    previewDocName = '';
    setPreviewMode(false, '');
}

// The header buttons belong to the preview while a document is open; the chat
// stream callbacks must not re-show the chat 💾 next to the preview's own 💾.
function isDocumentPreviewActive() {
    return !!previewDocId;
}
window.isDocumentPreviewActive = isDocumentPreviewActive;

function openDocumentPreview(docId, docName) {
    if (!docId) return;
    // The preview is its own main-window mode; a task view must release it.
    if (typeof closeTaskView === 'function') closeTaskView(true);
    const sidebar = document.querySelector('.sessions-sidebar');
    if (sidebar) sidebar.classList.remove('panel-open');
    previewDocId = docId;
    previewDocName = docName;
    setPreviewMode(true, docName);
    const saveButton = document.getElementById('save-document-button');
    if (saveButton) {
        saveButton.onclick = function() {
            if (previewDocId) window.location.href = '/api/documents/' + encodeURIComponent(previewDocId);
        };
    }
    const box = document.getElementById('document-preview');
    if (box) box.textContent = '…';
    fetch('/api/documents/' + encodeURIComponent(docId) + '/preview')
        .then(res => res.json().catch(() => ({})))
        .then(data => {
            if (!previewDocId || previewDocId !== docId) return; // another doc opened meanwhile
            renderDocumentPreview(data);
        })
        .catch(err => {
            if (box) box.textContent = t('document_preview_failed') + ': ' + err.message;
        });
}

function renderDocumentPreview(data) {
    const box = document.getElementById('document-preview');
    if (!box) return;
    box.innerHTML = '';
    if (data.error) {
        box.textContent = data.error;
        return;
    }
    if (data.kind === 'text') {
        if (data.extracted) {
            const note = document.createElement('div');
            note.className = 'document-preview-note';
            note.textContent = 'ℹ️ ' + t('document_extracted_text');
            box.appendChild(note);
        }
        const pre = document.createElement('pre');
        pre.className = 'document-preview-text';
        pre.textContent = data.text || '';
        box.appendChild(pre);
    } else if (data.kind === 'pdf') {
        const openLink = document.createElement('a');
        openLink.className = 'document-preview-open';
        openLink.href = data.url;
        openLink.target = '_blank';
        openLink.rel = 'noopener';
        openLink.textContent = '↗ ' + t('document_open_pdf');
        box.appendChild(openLink);
        const pages = Number(data.pages) || 0;
        const maxPages = Number(data.max_pages) || pages;
        const mobile = typeof window.isMobileViewport === 'function' && window.isMobileViewport();
        if (mobile) {
            // Chrome for Android cannot render a PDF in an iframe (it shows
            // «This content is blocked»); the server renders page images.
            if (pages > 0) {
                const pagesBox = document.createElement('div');
                pagesBox.className = 'document-preview-pages';
                const last = Math.min(pages, maxPages);
                for (let n = 1; n <= last; n += 1) {
                    const pageImg = document.createElement('img');
                    pageImg.className = 'document-preview-page';
                    pageImg.loading = 'lazy';
                    pageImg.src = (data.page_url || '') + n;
                    pageImg.alt = (data.filename || '') + ' — ' + n;
                    pagesBox.appendChild(pageImg);
                }
                box.appendChild(pagesBox);
                if (pages > last) {
                    const note = document.createElement('div');
                    note.className = 'document-preview-note';
                    note.textContent = 'ℹ️ ' + formatString(t('document_preview_pages_capped'), { n: last });
                    box.appendChild(note);
                }
            } else {
                const note = document.createElement('div');
                note.className = 'document-preview-note';
                note.textContent = t('document_preview_unavailable');
                box.appendChild(note);
            }
        } else {
            const frame = document.createElement('iframe');
            frame.className = 'document-preview-frame';
            frame.src = data.url;
            frame.title = data.filename || '';
            box.appendChild(frame);
        }
    } else if (data.kind === 'binary' && data.url) {
        if (String(data.mime || '').startsWith('image/')) {
            const img = document.createElement('img');
            img.className = 'document-preview-image';
            img.src = data.url;
            img.alt = data.filename || '';
            box.appendChild(img);
        } else {
            const frame = document.createElement('iframe');
            frame.className = 'document-preview-frame';
            frame.src = data.url;
            frame.title = data.filename || '';
            box.appendChild(frame);
        }
    } else {
        box.textContent = t('document_preview_unavailable');
    }
    if (data.truncated) {
        const note = document.createElement('div');
        note.className = 'document-preview-note';
        note.textContent = 'ℹ️ ' + t('document_preview_truncated');
        box.appendChild(note);
    }
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && previewDocId) closeDocumentPreview();
});

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
// The highlight is shown only while the "Deep analysis" toggle is on, so a
// cleared selection or an off toggle can never leave a stale green tint.
function applyRlmMarkers() {
    document.querySelectorAll('.document-item').forEach(item => {
        const id = item.dataset.documentId;
        const selected = rlmSelectedDocs.has(id);
        const show = isRlmMode() && selected;
        item.classList.toggle('rlm-selected', show);
        const marker = item.querySelector('.rlm-marker');
        if (marker) marker.textContent = show ? ' ✓' : '';
    });
}

// Pick a document with the single checkbox set. The pick is mirrored into the
// deep-analysis set in EVERY mode, so the analysis corpus always equals the
// visible checkbox selection and cannot drift apart from it.
function setDocChecked(docId, checked) {
    if (checked) {
        selectedDocIds.add(docId);
        rlmSelectedDocs.add(docId);
    } else {
        selectedDocIds.delete(docId);
        rlmSelectedDocs.delete(docId);
    }
    if (!applyingCorpusFilter) corpusGroupNames = [];
    applyRlmMarkers();
    updateRlmToggleCount();
    syncRlmDocsSelect();
    if (typeof window.renderCorpusChip === 'function') window.renderCorpusChip();
}

// The corpus chip: how many documents are ticked and, when the ticked set is
// exactly whole folders (one or more), which groups those are.
window.getCorpusSummary = function() {
    const count = rlmSelectedDocs.size;
    if (corpusGroupNames.length) {
        return { count: count, groups: corpusGroupNames.slice() };
    }
    const groups = [];
    if (count > 0) {
        const byGroup = {};
        Object.values(documentsData).forEach(doc => {
            const key = doc.folder_id || ROOT_GROUP;
            (byGroup[key] = byGroup[key] || []).push(doc.id);
        });
        Object.keys(byGroup).forEach(key => {
            const ids = byGroup[key];
            if (ids.length && ids.every(id => rlmSelectedDocs.has(id))) {
                const name = key === ROOT_GROUP ? t('Unfiled') : (foldersData[key] ? foldersData[key].name : key);
                groups.push({ key: key, name: name, size: ids.length });
            }
        });
    }
    const grouped = groups.reduce((total, group) => total + group.size, 0);
    return {
        count: count,
        groups: grouped === count ? groups.map(group => group.name) : [],
    };
};

// Bulk selection used by the scope popover: tick every document of the chosen
// folders (plus the root when asked) uploaded inside the date range. The
// selection is a snapshot of document ids; a document added to the folder
// later is not included until the filter is applied again.
window.getSelectedDocIds = function() {
    return Array.from(rlmSelectedDocs);
};

// Replace the whole corpus with an explicit id set. The scope popover uses it
// for live folder/date edits: the Documents checkboxes, the bulk bar and the
// chip all follow immediately, with no «Apply» step.
window.setCorpusSelection = function(ids) {
    const wanted = new Set((ids || []).map(String));
    applyingCorpusFilter = true;
    try {
        clearDocumentSelection();
        // Ids come from the scope's own /api/documents payload; the Documents
        // panel may not have rendered yet, so do not require documentsData.
        wanted.forEach(id => setDocChecked(id, true));
    } finally {
        applyingCorpusFilter = false;
    }
    document.querySelectorAll('.doc-check[data-document-id]').forEach(box => {
        box.checked = rlmSelectedDocs.has(box.dataset.documentId);
    });
    updateBulkBar();
    updateFolderCheckboxes();
    if (typeof window.renderCorpusChip === 'function') window.renderCorpusChip();
    return rlmSelectedDocs.size;
};

window.applyCorpusFilter = function(options) {
    const folderIds = new Set((options && options.folderIds) || []);
    const unfiled = !!(options && options.unfiled);
    const from = options && options.from ? new Date(options.from + 'T00:00:00').getTime() : null;
    const to = options && options.to ? new Date(options.to + 'T23:59:59.999').getTime() : null;
    const matches = Object.values(documentsData).filter(doc => {
        const root = !doc.folder_id;
        if (root ? !unfiled : !folderIds.has(doc.folder_id)) return false;
        const stamp = Date.parse(doc.uploaded_at || '');
        if (from !== null && !(stamp >= from)) return false;
        if (to !== null && !(stamp <= to)) return false;
        return true;
    });
    if (!matches.length) return 0;
    const matched = new Set(matches.map(doc => doc.id));
    const byGroup = {};
    Object.values(documentsData).forEach(doc => {
        const key = doc.folder_id || ROOT_GROUP;
        (byGroup[key] = byGroup[key] || []).push(doc.id);
    });
    const groupNames = [];
    folderIds.forEach(id => {
        const ids = byGroup[id] || [];
        if (ids.length && ids.every(docId => matched.has(docId))) {
            groupNames.push(foldersData[id] ? foldersData[id].name : id);
        }
    });
    if (unfiled) {
        const ids = byGroup[ROOT_GROUP] || [];
        if (ids.length && ids.every(docId => matched.has(docId))) groupNames.push(t('Unfiled'));
    }
    applyingCorpusFilter = true;
    try {
        clearDocumentSelection();
        matches.forEach(doc => setDocChecked(doc.id, true));
    } finally {
        applyingCorpusFilter = false;
    }
    corpusGroupNames = groupNames;
    document.querySelectorAll('.doc-check[data-document-id]').forEach(box => {
        box.checked = rlmSelectedDocs.has(box.dataset.documentId);
    });
    updateBulkBar();
    updateFolderCheckboxes();
    if (typeof window.renderCorpusChip === 'function') window.renderCorpusChip();
    return matches.length;
};

window.clearCorpusFilter = function() {
    clearDocumentSelection();
    if (typeof window.renderCorpusChip === 'function') window.renderCorpusChip();
};

// When the toggle is switched on, the current checkbox selection becomes the
// deep-analysis corpus (previously-selected RLM docs are not dropped).
function syncRlmFromSelection() {
    rlmSelectedDocs = new Set(selectedDocIds);
    applyRlmMarkers();
    updateRlmToggleCount();
    syncRlmDocsSelect();
}

// Delete every selected document. Confirms with the count and total size,
// then issues one DELETE per id and reloads the list once all settle.
function deleteSelectedDocuments() {
    const ids = Array.from(selectedDocIds);
    if (ids.length === 0) return;
    let totalSize = 0;
    ids.forEach(id => {
        const doc = documentsData[id];
        if (doc && doc.file_size) totalSize += doc.file_size;
    });
    const confirmMessage = formatString(t('delete_selected_confirm'), { count: ids.length, size: formatFileSize(totalSize) });
    if (!confirm(confirmMessage)) return;

    let failures = 0;
    let done = 0;
    ids.forEach(id => {
        fetchWithCSRF(`/api/documents/${id}`, { method: 'DELETE' })
            .then(res => res.json())
            .then(data => {
                if (data.status !== 'ok') failures++;
            })
            .catch(() => failures++)
            .finally(() => {
                done++;
                if (done === ids.length) {
                    clearDocumentSelection();
                    loadDocuments(false);
                    if (failures > 0) {
                        alert(t('error') + ': ' + t('delete_selected_failure'));
                    }
                }
            });
    });
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
        if (savedView && ['sessions', 'documents', 'tasks'].includes(savedView)) {
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
    // selection into the RLM corpus; switching it off immediately clears the
    // green highlight and the count (the corpus is re-derived on re-enable).
    const rlmToggleEl = document.getElementById('rlm-toggle');
    if (rlmToggleEl) {
        rlmToggleEl.addEventListener('change', function() {
            if (rlmToggleEl.checked) {
                syncRlmFromSelection();
            } else {
                // The tool changes, the corpus does not: the ticked documents
                // keep scoping chat RAG and tasks when deep analysis is off.
                applyRlmMarkers();
                updateRlmToggleCount();
                updateBulkBar();
            }
            if (typeof window.renderCorpusChip === 'function') window.renderCorpusChip();
        });
    }

    // Apply the initial view (synchronizes UI with currentView)
    applyCurrentView();
}
