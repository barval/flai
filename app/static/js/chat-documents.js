// app/static/js/chat-documents.js
// Document management functions with index status, processing time display,
// periodic updates, and blinking animation for indexing documents.

let currentView = 'sessions'; // 'sessions' or 'documents'
let documentsData = {};
let documentsPollingInterval = null;

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
        stopDocumentsPolling();
    } else {
        sessionsList.classList.remove('active');
        sessionsList.style.display = 'none';
        documentsList.classList.remove('hidden');
        documentsList.classList.add('active');
        loadDocuments(); // immediate load
        startDocumentsPolling();
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

function startDocumentsPolling() {
    if (documentsPollingInterval) clearInterval(documentsPollingInterval);
    // Poll server every 10 seconds
    documentsPollingInterval = setInterval(() => {
        if (currentView === 'documents') {
            loadDocuments(false); // silent update
        } else {
            stopDocumentsPolling();
        }
    }, 10000);

    // Start live timer for elapsed time display (updates every second)
    startLiveDocumentTimer();
}

function stopDocumentsPolling() {
    if (documentsPollingInterval) {
        clearInterval(documentsPollingInterval);
        documentsPollingInterval = null;
    }
    stopLiveDocumentTimer();
}

// Live timer — updates elapsed time display every second without server requests
let documentLiveTimer = null;
function startLiveDocumentTimer() {
    stopLiveDocumentTimer();
    documentLiveTimer = setInterval(() => {
        const indexingDocs = document.querySelectorAll('.document-item[data-indexing-start]');
        indexingDocs.forEach(el => {
            const startVal = el.dataset.indexingStart;
            if (!startVal || startVal === '') return;
            const startTime = parseInt(startVal, 10);
            if (isNaN(startTime) || startTime <= 0) return;
            const elapsed = Math.floor((Date.now() - startTime * 1000) / 1000);
            if (isNaN(elapsed) || elapsed < 0) return;
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            const timeStr = mins > 0 ? `${mins}м ${secs}с` : `${secs}с`;
            const timerEl = el.querySelector('.doc-live-timer');
            if (timerEl) {
                timerEl.textContent = ` ⏱️ ${timeStr}`;
            }
        });
    }, 1000);
}

function stopLiveDocumentTimer() {
    if (documentLiveTimer) {
        clearInterval(documentLiveTimer);
        documentLiveTimer = null;
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
        })
        .catch(err => {
            console.error('Error loading documents:', err);
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
        // Add blink class if indexing for the main status icon
        const iconClass = isIndexing ? 'document-status-icon blink' : 'document-status-icon';

        // Show live elapsed time for documents being indexed
        // Show hourglass indicator for queued documents
        let statusIndicator = '';
        let indexingStartTimestamp = '';
        if (isIndexing && doc.indexing_started_at) {
            // Calculate elapsed time since indexing started
            const startTime = new Date(doc.indexing_started_at.replace(' ', 'T') + 'Z');
            if (!isNaN(startTime.getTime())) {
                indexingStartTimestamp = Math.floor(startTime.getTime() / 1000).toString();
                const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
                const mins = Math.floor(elapsed / 60);
                const secs = elapsed % 60;
                const timeStr = mins > 0 ? `${mins}м ${secs}с` : `${secs}с`;
                statusIndicator = ` ⏱️ ${timeStr}`;
            }
        } else if (isPending) {
            statusIndicator = ' ⏳';
        }

        // Show processing_time for completed documents
        let processingTimeStr = '';
        if (doc.processing_time !== null && doc.processing_time !== undefined) {
            const minAbbr = t('minutes_abbr');
            processingTimeStr = ` ⏱️ ${doc.processing_time.toFixed(1)}${minAbbr}`;
        }

        // Embedding model line - always show with a fixed 🔄 icon, regardless of status
        let displayModel = doc.embedding_model || window.CURRENT_EMBEDDING_MODEL || '';
        let embeddingLine = '';
        if (displayModel) {
            // Use a fixed icon '🔄' for the embedding model line, with no status-dependent class.
            embeddingLine = `<div class="document-embedding"><span class="document-status-icon" style="margin-right:4px;">🔄</span> ${displayModel}</div>`;
        }

        html += `
        <div class="document-item" data-document-id="${doc.id}" data-document-name="${escapeHtml(doc.filename)}" data-indexing-start="${indexingStartTimestamp}">
            <div class="document-content">
                <div class="document-info">
                    <div class="document-title">
                        <span class="${iconClass}" title="${statusTitle}">${statusIcon}</span>
                        📄 ${escapeHtml(doc.filename)}
                    </div>
                    <div class="document-date">📅 ${dateStr} ${fileSizeFormatted ? '[' + fileSizeFormatted + ']' : ''}<span class="doc-live-timer">${statusIndicator}</span>${processingTimeStr}</div>
                    ${embeddingLine}
                </div>
                <button class="delete-document-button" title="${t('delete_document')}">🗑️</button>
            </div>
        </div>
        `;
    });

    documentsList.innerHTML = html;
    if (documentsCount) {
        documentsCount.textContent = documents.length;
    }

    attachDocumentEventHandlers();
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
                alert(t('document_uploaded'));
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
            fileInput.accept = '.pdf,.doc,.docx,.txt';
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