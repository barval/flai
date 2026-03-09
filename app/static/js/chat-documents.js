// app/static/js/chat-documents.js
// Document management functions with index status

let currentView = 'sessions'; // 'sessions' or 'documents'
let documentsData = {};

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
        sessionsList.style.display = 'block';
        documentsList.style.display = 'none';
    } else {
        sessionsList.style.display = 'none';
        documentsList.style.display = 'block';
        loadDocuments();
    }

    // Save preference
    const login = window.CURRENT_USER_LOGIN;
    if (login) {
        localStorage.setItem(`current_view_${login}`, view);
    }
}

function loadDocuments() {
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
    switch (status) {
        case 'pending':
            return 'Pending indexing';
        case 'indexing':
            return 'Indexing in progress';
        case 'indexed':
            return 'Indexed';
        case 'failed':
            return 'Indexing failed';
        default:
            return 'Not indexed';
    }
}

function updateDocumentsList(documents) {
    const documentsList = document.getElementById('documents-list');
    const documentsCount = document.getElementById('documents-count');

    documents.sort((a, b) => new Date(b.uploaded_at) - new Date(a.uploaded_at));

    let html = '';
    documents.forEach(doc => {
        const dateStr = doc.uploaded_at ? formatFullDateTime(doc.uploaded_at) : '';
        const statusIcon = getStatusIcon(doc.index_status);
        const statusTitle = getStatusTitle(doc.index_status);
        html += `
        <div class="document-item" data-document-id="${doc.id}" data-document-name="${escapeHtml(doc.filename)}">
            <div class="document-content">
                <div class="document-info">
                    <div class="document-title">
                        <span class="document-status-icon" title="${statusTitle}">${statusIcon}</span>
                        📄 ${escapeHtml(doc.filename)}
                    </div>
                    <div class="document-date">📅 ${dateStr}</div>
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
    document.querySelectorAll('.document-item').forEach(el => {
        el.addEventListener('click', function(e) {
            if (e.target.closest('.delete-document-button')) return;
            const docId = this.dataset.documentId;
            downloadDocument(docId);
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
}

function downloadDocument(docId) {
    window.open(`/api/documents/${docId}`, '_blank');
}

function deleteDocument(docId, docName) {
    const confirmMessage = formatString(t('delete_document_confirm'), {
        filename: docName
    });
    if (!confirm(confirmMessage)) return;

    fetch(`/api/documents/${docId}`, { method: 'DELETE' })
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

    fetch('/api/documents/upload', {
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

    // Initialize with current view
    switchView(currentView);
}