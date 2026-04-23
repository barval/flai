// app/static/js/admin-backups.js
// Backup/restore functionality for admin panel

document.addEventListener('DOMContentLoaded', function() {
    const createBtn = document.getElementById('create-backup-btn');
    const refreshBtn = document.getElementById('refresh-backups-btn');
    const backupsTbody = document.getElementById('backups-tbody');
    const noBackupsMsg = document.getElementById('no-backups-msg');
    const backupsTable = document.getElementById('backups-table');

    if (!createBtn) return; // Not on backups tab

    // Load backups on tab activation
    const backupsTabBtn = document.querySelector('[data-tab="backups"]');
    if (backupsTabBtn) {
        backupsTabBtn.addEventListener('click', function() {
            loadBackups();
        });
    }

    // Initial load
    loadBackups();

    createBtn.addEventListener('click', createBackup);
    refreshBtn.addEventListener('click', refreshBackups);

    function refreshBackups() {
        refreshBtn.disabled = true;
        refreshBtn.textContent = '⏳';
        loadBackups().finally(() => {
            refreshBtn.disabled = false;
            refreshBtn.textContent = '🔄';
        });
    }

    function loadBackups() {
        return fetch('/admin/api/backups/')
            .then(res => res.json())
            .then(backups => {
                backupsTbody.innerHTML = '';
                if (!backups || backups.length === 0) {
                    backupsTable.style.display = 'none';
                    noBackupsMsg.style.display = 'block';
                    return;
                }
                backupsTable.style.display = 'table';
                noBackupsMsg.style.display = 'none';

                backups.forEach(backup => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td title="${escapeHtml(backup.filename)}">${escapeHtml(backup.filename)}</td>
                        <td><span class="backup-type-badge ${backup.type}">${backup.type === 'users' ? t('Users only') : (backup.type === 'full' ? t('Full (users + chats + files)') : backup.type)}</span></td>
                        <td>${formatSize(backup.size)}</td>
                        <td>${formatDate(backup.created_at)}</td>
                        <td class="actions-cell">
                            <button class="btn-success" onclick="restoreBackup('${escapeHtml(backup.filename)}')" title="${t('restore')}">${t('restore')}</button>
                            <a href="/admin/api/backups/${encodeURIComponent(backup.filename)}/download" class="btn-warning" title="${t('download')}" style="text-decoration:none;padding:5px 10px;border-radius:4px;color:white;display:inline-block;">${t('download')}</a>
                            <button class="btn-danger" onclick="deleteBackup('${escapeHtml(backup.filename)}')" title="${t('delete')}">${t('delete')}</button>
                        </td>
                    `;
                    backupsTbody.appendChild(tr);
                });
            })
            .catch(err => {
                console.error('Error loading backups:', err);
                alert(t('backup_error'));
            })
            .finally(() => {});  // Return a promise for refreshBackups().finally()
    }

    function createBackup() {
        const backupType = document.getElementById('backup-type').value;
        createBtn.disabled = true;
        createBtn.textContent = t('creating');

        fetchWithCSRF('/admin/api/backups/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: backupType })
        })
        .then(res => res.json())
        .then(result => {
            if (result.status === 'ok') {
                loadBackups();
            } else {
                showToast(t('backup_error') + ': ' + (result.error || ''), 'error');
            }
        })
        .catch(err => {
            console.error('Error creating backup:', err);
            alert(t('backup_error'));
        })
        .finally(() => {
            createBtn.disabled = false;
            createBtn.textContent = t('Create backup');
        });
    }

    window.restoreBackup = function(filename) {
        const backupType = filename.startsWith('full_') ? 'full' : 'users';
        const msg = backupType === 'full'
            ? t('restore_confirm_full').replace('{filename}', filename)
            : t('restore_confirm_users').replace('{filename}', filename);

        if (!confirm(msg)) return;

        createBtn.disabled = true;

        fetchWithCSRF('/admin/api/backups/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: filename })
        })
        .then(res => res.json())
        .then(result => {
            if (result.status === 'ok') {
                setTimeout(() => location.reload(), 1000);
            } else {
                alert(t('restore_error') + ': ' + (result.error || ''));
                createBtn.disabled = false;
            }
        })
        .catch(err => {
            console.error('Error restoring backup:', err);
            alert(t('restore_error'));
            createBtn.disabled = false;
        });
    };

    window.deleteBackup = function(filename) {
        const msg = t('delete_confirm').replace('{filename}', filename);
        if (!confirm(msg)) return;

        fetchWithCSRF(`/admin/api/backups/${encodeURIComponent(filename)}`, {
            method: 'DELETE'
        })
        .then(res => res.json())
        .then(result => {
            if (result.status === 'ok') {
                loadBackups();
            } else {
                alert(t('delete_error') + ': ' + (result.error || ''));
            }
        })
        .catch(err => {
            console.error('Error deleting backup:', err);
            alert(t('delete_error'));
        });
    };

    // Utility: translation helper
    function t(key) {
        return (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function formatDate(isoStr) {
        try {
            const d = new Date(isoStr);
            return d.toLocaleString();
        } catch {
            return isoStr;
        }
    }
});
