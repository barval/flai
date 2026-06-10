// app/static/js/admin-cameras.js
// Camera rooms — sync from room-snapshot-api + preview thumbnails

const CAM_CACHE_PREFIX = 'cam_preview_';

document.addEventListener('DOMContentLoaded', function() {
    loadCameras(false);
    setupSyncButton();
    setupPreviewModal();
});

function setupSyncButton() {
    const btn = document.getElementById('sync-cameras-btn');
    if (btn) {
        btn.addEventListener('click', syncCameras);
    }
}

function setSyncing(btn, active) {
    if (!btn) return;
    btn.disabled = active;
    if (active) {
        btn.classList.add('btn-syncing');
        btn.textContent = '🔄 ' + t('Loading...');
    } else {
        btn.classList.remove('btn-syncing');
        btn.textContent = '🔄 ' + t('Update camera list');
    }
}

function syncCameras() {
    const btn = document.getElementById('sync-cameras-btn');
    setSyncing(btn, true);

    clearImageCache();

    fetchWithCSRF('/admin/api/cameras/sync', { method: 'POST' })
    .then(r => r.json())
    .then(result => {
        if (result.status === 'ok') {
            loadCameras(true);
        } else {
            setSyncing(btn, false);
            alert(t('error') + ': ' + (result.error || t('unknown_error')));
        }
    })
    .catch(err => {
        console.error('Error syncing cameras:', err);
        setSyncing(btn, false);
        alert(t('Sync error'));
    });
}

function loadCameras(syncing) {
    const btn = document.getElementById('sync-cameras-btn');
    let pendingLoads = 0;

    function onPreviewDone() {
        pendingLoads--;
        if (syncing && pendingLoads <= 0) {
            setSyncing(btn, false);
        }
    }

    fetch('/admin/api/cameras')
    .then(response => response.json())
    .then(rooms => {
        const tbody = document.getElementById('cameras-tbody');
        const noMsg = document.getElementById('no-cameras-msg');
        if (!rooms || rooms.length === 0) {
            tbody.innerHTML = '';
            if (noMsg) noMsg.style.display = 'block';
            if (syncing) setSyncing(btn, false);
            return;
        }
        if (noMsg) noMsg.style.display = 'none';
        tbody.innerHTML = '';

        rooms.forEach((room, index) => {
            const row = document.createElement('tr');
            row.dataset.code = room.code;

            // Row number
            const numCell = document.createElement('td');
            numCell.textContent = index + 1;
            numCell.className = 'cam-num';
            row.appendChild(numCell);

            // Status checkbox
            const statusCell = document.createElement('td');
            const statusCheck = document.createElement('input');
            statusCheck.type = 'checkbox';
            statusCheck.checked = room.enabled;
            statusCheck.addEventListener('change', async function() {
                const checkbox = this;
                try {
                    const resp = await fetchWithCSRF('/admin/api/cameras/' + room.code + '/toggle', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled: checkbox.checked })
                    });
                    const result = await resp.json();
                    if (result.status !== 'ok') {
                        checkbox.checked = !checkbox.checked;
                        alert(t('error') + ': ' + (result.error || t('unknown_error')));
                    }
                } catch (err) {
                    checkbox.checked = !checkbox.checked;
                    alert(t('error') + ': ' + err.message);
                }
            });
            statusCell.appendChild(statusCheck);
            row.appendChild(statusCell);

            // Code
            const codeCell = document.createElement('td');
            codeCell.textContent = room.code;
            codeCell.className = 'cam-code';
            row.appendChild(codeCell);

            // Room (name)
            const roomCell = document.createElement('td');
            const forms = room.name_forms || [];
            const bold = document.createElement('b');
            bold.textContent = forms[0] || room.code;
            roomCell.appendChild(bold);
            if (forms.length > 1) {
                const rest = document.createElement('span');
                rest.textContent = ` (${forms.slice(1).join(', ')})`;
                roomCell.appendChild(rest);
            }
            row.appendChild(roomCell);

            // Preview thumbnail
            const previewCell = document.createElement('td');
            previewCell.className = 'cam-preview-cell';
            const img = document.createElement('img');
            img.className = 'cam-preview';
            img.alt = room.code;
            img.width = 192;
            img.height = 108;

            const proxyUrl = '/admin/api/cameras/' + room.code + '/proxy';

            // Spinner shown while image loads
            const spinner = document.createElement('div');
            spinner.className = 'cam-spinner';

            const cached = getCachedImage(room.code);
            if (cached) {
                img.src = cached;
                previewCell.classList.add('cam-online');
            } else if (syncing) {
                // Only fetch from proxy when user clicked "Update" button
                img.src = proxyUrl;
                previewCell.appendChild(spinner);
                pendingLoads++;
            } else {
                // Initial page load — no cache, show placeholder
                img.style.display = 'none';
                const placeholder = document.createElement('span');
                placeholder.className = 'cam-preview-error';
                placeholder.textContent = '—';
                previewCell.appendChild(placeholder);
            }

            img.onload = function() {
                const s = previewCell.querySelector('.cam-spinner');
                if (s) s.remove();
                previewCell.classList.add('cam-online');
                previewCell.classList.remove('cam-offline');
                if (!cached && this.src.includes('/proxy') && !this.src.startsWith('data:')) {
                    cacheImage(room.code, this);
                }
                if (cached) return;
                onPreviewDone();
            };
            img.onerror = function() {
                const s = previewCell.querySelector('.cam-spinner');
                if (s) s.remove();
                if (this.dataset.retried !== 'true') {
                    this.dataset.retried = 'true';
                    setTimeout(() => {
                        this.src = proxyUrl + '?t=' + Date.now();
                    }, 1000);
                    return;
                }
                previewCell.classList.add('cam-offline');
                previewCell.classList.remove('cam-online');
                this.style.display = 'none';
                const placeholder = document.createElement('span');
                placeholder.className = 'cam-preview-error';
                placeholder.textContent = '—';
                previewCell.appendChild(placeholder);
                onPreviewDone();
            };

            img.onclick = function() {
                openPreviewModal(this.src, room.code);
            };

            previewCell.appendChild(img);
            row.appendChild(previewCell);

            tbody.appendChild(row);
        });

        // If all images were cached, no pending loads — stop syncing immediately
        if (syncing && pendingLoads <= 0) {
            setSyncing(btn, false);
        }
    })
    .catch(err => {
        console.error('Error loading cameras:', err);
        if (syncing) setSyncing(btn, false);
    });
}

function getCachedImage(code) {
    try {
        const raw = localStorage.getItem(CAM_CACHE_PREFIX + code);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed.data;
    } catch {
        return null;
    }
}

function cacheImage(code, imgElement) {
    try {
        const canvas = document.createElement('canvas');
        canvas.width = imgElement.naturalWidth || imgElement.width;
        canvas.height = imgElement.naturalHeight || imgElement.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(imgElement, 0, 0);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
        localStorage.setItem(CAM_CACHE_PREFIX + code, JSON.stringify({
            data: dataUrl,
            ts: Date.now()
        }));
    } catch (e) {
        clearImageCache();
    }
}

function clearImageCache() {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && key.startsWith(CAM_CACHE_PREFIX)) {
            keys.push(key);
        }
    }
    keys.forEach(k => localStorage.removeItem(k));
}

function setupPreviewModal() {
    if (!document.getElementById('preview-modal')) {
        const modal = document.createElement('div');
        modal.id = 'preview-modal';
        modal.className = 'preview-modal';
        modal.innerHTML = '<span class="preview-modal-close">&times;</span><img id="preview-modal-img" src="" alt="">';
        document.body.appendChild(modal);

        modal.querySelector('.preview-modal-close').onclick = () => {
            modal.style.display = 'none';
        };
        modal.onclick = (e) => {
            if (e.target === modal) modal.style.display = 'none';
        };
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') modal.style.display = 'none';
        });
    }
}

function openPreviewModal(src, code) {
    const modal = document.getElementById('preview-modal');
    const img = document.getElementById('preview-modal-img');
    if (modal && img) {
        img.src = src;
        img.alt = code;
        modal.style.display = 'flex';
    }
}
