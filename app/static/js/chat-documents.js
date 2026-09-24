// app/static/js/chat-documents.js
// Document management functions with index status, processing time display,
// periodic updates, and blinking animation for indexing documents.

let currentView = 'sessions'; // 'sessions' or 'documents'
let documentsData = {};
let documentTimerInterval = null;
let documentQueuePositions = {};

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
        .then(documents => {
            documentsData = {};
            documents.forEach(doc => {
                documentsData[doc.id] = doc;
            });
            updateDocumentsList(documents);
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

function updateDocumentsList(documents) {
    const documentsList = document.getElementById('documents-list');
    const documentsCount = document.getElementById('documents-count');

    // Drop RLM selections pointing to documents that no longer exist
    // (e.g. deleted while selected).
    const liveIds = new Set(documents.map(d => d.id));
    for (const id of rlmSelectedDocs) {
        if (!liveIds.has(id)) rlmSelectedDocs.delete(id);
    }

    documents.sort((a, b) => new Date(b.uploaded_at) - new Date(a.uploaded_at));

    let html = '';
    documents.forEach(doc => {
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

        html += `
        <div class="document-item${isRlmSelected ? ' rlm-selected' : ''}" data-document-id="${doc.id}" data-document-name="${escapeHtml(doc.filename)}" data-index-status="${escapeHtml(doc.index_status || '')}">
            <div class="document-content">
                <div class="document-info">
                    <div class="document-title">
                        <span class="${iconClass}" title="${statusTitle}${queuePosition > 0 ? ` (#${queuePosition})` : ''}">${isPending && queuePosition > 0 ? `⏳ ${queuePosition}` : statusIcon}</span>
                        📄 ${escapeHtml(doc.filename)}<span class="rlm-marker">${isRlmSelected ? ' ✓' : ''}</span>
                    </div>
                    <div class="document-date">📅 ${dateStr} ${fileSizeFormatted ? '[' + fileSizeFormatted + ']' : ''}<span class="doc-live-timer"${indexingStartTimestamp}>${statusIndicator || processingTimeStr}</span></div>
                    ${embeddingLine}
                    ${descriptionLine}
                </div>
                <button class="delete-document-button" title="${t('delete_document')}">🗑️</button>
            </div>
        </div>
        `;
    });

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
    // Clicking a document toggles its selection for "Deep analysis" (RLM).
    document.querySelectorAll('.document-item').forEach(item => {
        item.addEventListener('click', function() {
            toggleRlmDocSelection(this);
        });
    });
}

// Toggle a document in/out of the RLM "Deep analysis" selection.
function toggleRlmDocSelection(item) {
    const docId = item.dataset.documentId;
    if (!docId) return;
    if (rlmSelectedDocs.has(docId)) {
        rlmSelectedDocs.delete(docId);
    } else {
        rlmSelectedDocs.add(docId);
    }
    const selected = rlmSelectedDocs.has(docId);
    item.classList.toggle('rlm-selected', selected);
    const marker = item.querySelector('.rlm-marker');
    if (marker) marker.textContent = selected ? ' ✓' : '';
    // Mirror into the hidden #rlm-docs multi-select (source of truth for
    // sendRlmAnalysis which reads selectedOptions).
    const rlmDocs = document.getElementById('rlm-docs');
    if (rlmDocs) {
        for (const opt of rlmDocs.options) {
            if (opt.value === docId) opt.selected = selected;
        }
    }
    updateRlmToggleCount();
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

function uploadDocument(file) {
    const formData = new FormData();
    formData.append('file', file);

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

    // Set up new document button
    const newDocBtn = document.getElementById('new-document-button');
    if (newDocBtn) {
        newDocBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            const fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.accept = '.pdf,.doc,.docx,.txt,.odt,.rtf,.csv,.json,.epub';
            fileInput.onchange = function(e) {
                if (e.target.files.length > 0) {
                    uploadDocument(e.target.files[0]);
                }
            };
            fileInput.click();
        });
    }

    // Apply the initial view (synchronizes UI with currentView)
    applyCurrentView();
}
