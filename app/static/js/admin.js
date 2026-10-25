// app/static/js/admin.js
// Admin panel JavaScript - handles user management, translations, and page-specific setup

// Phone cards (CSS <=768px): the header row is hidden, so every cell needs its
// own localized heading. The camera column only exists when rooms are configured.
const USER_CARD_LABELS = [
    'Status', 'Login', 'Password', 'Name', 'Class', 'Storage', 'Sessions', 'Tasks',
    'Outgoing tokens', 'Incoming tokens', 'Messages', 'Files', 'Documents', 'SLM Facts',
    'Internet requests', 'Tavily API', 'FLAI API',
];

function labelUserCardCells(row) {
    const cells = Array.from(row.children);
    const labels = cells.length === USER_CARD_LABELS.length + 2
        ? [...USER_CARD_LABELS, 'Camera access', 'Actions']
        : [...USER_CARD_LABELS, 'Actions'];
    cells.forEach((cell, index) => {
        if (labels[index]) cell.dataset.label = t(labels[index]);
    });
}

let currentSortField = null;
let currentSortDirection = 'asc';

document.addEventListener('DOMContentLoaded', function() {
    dlog('Admin page loaded');
    document.body.classList.add('admin-page');

    loadUsers();
    setupModals();
    setupSortableHeaders();
    loadHardware();

    const refreshHardwareBtn = document.getElementById('refresh-hardware-btn');
    if (refreshHardwareBtn) {
        refreshHardwareBtn.addEventListener('click', loadHardware);
    }

    // Admin stats: refresh periodically (SSE is chat-specific, not used here)
    setInterval(refreshStats, 30000);
});

function setupSortableHeaders() {
    const headers = document.querySelectorAll('#users-table th[data-sort]');
    headers.forEach(header => {
        header.style.cursor = 'pointer';
        header.style.userSelect = 'none';
        header.addEventListener('click', function() {
            const field = this.dataset.sort;
            if (currentSortField === field) {
                currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                currentSortField = field;
                currentSortDirection = 'asc';
            }
            // Update visual indicators
            headers.forEach(h => {
                const existingIndicator = h.querySelector('.sort-indicator');
                if (existingIndicator) existingIndicator.remove();
            });
            const indicator = document.createElement('span');
            indicator.className = 'sort-indicator';
            indicator.textContent = currentSortDirection === 'asc' ? ' ▲' : ' ▼';
            this.appendChild(indicator);
            // Re-sort and render
            loadUsers();
        });
    });
}

function loadUsers() {
    fetch('/admin/api/users')
    .then(response => response.json())
    .then(users => {
        dlog('Users loaded:', users);
        // Sort users if a sort field is selected
        if (currentSortField) {
            users.sort((a, b) => {
                let valA = a[currentSortField];
                let valB = b[currentSortField];
                // Handle null/undefined values
                if (valA === null || valA === undefined) valA = '';
                if (valB === null || valB === undefined) valB = '';
                // Convert to comparable types
                if (typeof valA === 'number' && typeof valB === 'number') {
                    return currentSortDirection === 'asc' ? valA - valB : valB - valA;
                }
                // String comparison
                valA = String(valA).toLowerCase();
                valB = String(valB).toLowerCase();
                if (currentSortDirection === 'asc') {
                    return valA.localeCompare(valB, undefined, {numeric: true});
                } else {
                    return valB.localeCompare(valA, undefined, {numeric: true});
                }
            });
        }
        const tbody = document.getElementById('users-tbody');
        tbody.innerHTML = '';
        const tavilyCells = new Map();
        users.forEach(user => {
            const row = document.createElement('tr');
            row.dataset.login = user.login;
            if (!user.is_active) {
                row.classList.add('user-inactive');
            }
            const statusCell = document.createElement('td');
            const statusCheck = document.createElement('input');
            statusCheck.type = 'checkbox';
            statusCheck.checked = user.is_active == 1;
            statusCheck.addEventListener('change', () => {
                updateUserField(user.login, 'is_active', statusCheck.checked);
                if (statusCheck.checked) {
                    row.classList.remove('user-inactive');
                } else {
                    row.classList.add('user-inactive');
                }
            });
            statusCell.appendChild(statusCheck);
            row.appendChild(statusCell);
            const loginCell = document.createElement('td');
            loginCell.textContent = user.login;
            row.appendChild(loginCell);
            const passCell = document.createElement('td');
            const changePassBtn = document.createElement('button');
            changePassBtn.textContent = t('change');
            changePassBtn.title = t('change_password');
            changePassBtn.className = 'change-password-btn';
            changePassBtn.onclick = () => openPasswordModal(user.login);
            passCell.appendChild(changePassBtn);
            row.appendChild(passCell);
            const nameCell = document.createElement('td');
            const nameInput = document.createElement('input');
            nameInput.type = 'text';
            nameInput.value = user.name;
            nameInput.addEventListener('change', () => updateUserField(user.login, 'name', nameInput.value));
            nameCell.appendChild(nameInput);
            row.appendChild(nameCell);
            const classCell = document.createElement('td');
            const classSelect = document.createElement('select');
            [0,1,2].forEach(val => {
                const opt = document.createElement('option');
                opt.value = val;
                opt.textContent = val;
                if (val == user.service_class) opt.selected = true;
                classSelect.appendChild(opt);
            });
            classSelect.addEventListener('change', () => updateUserField(user.login, 'service_class', parseInt(classSelect.value)));
            classCell.appendChild(classSelect);
            row.appendChild(classCell);
            const storageCell = document.createElement('td');
            storageCell.className = 'storage-cell';
            const storageUsedMb = (user.storage_used || 0) / (1024 * 1024);
            const storageLimitMb = user.storage_limit_mb || 500;
            const storageUsedText = document.createElement('span');
            storageUsedText.className = 'storage-used';
            storageUsedText.textContent = `${storageUsedMb.toFixed(1)} /`;
            storageCell.appendChild(storageUsedText);

            const storageLimitInput = document.createElement('input');
            storageLimitInput.type = 'number';
            storageLimitInput.min = '1';
            storageLimitInput.step = '1';
            storageLimitInput.className = 'storage-limit-input';
            storageLimitInput.value = storageLimitMb;
            storageLimitInput.dataset.default = user.storage_limit_default_mb || storageLimitMb;
            storageLimitInput.placeholder = storageLimitInput.dataset.default;
            storageLimitInput.title = t('storage_limit_hint').replace(
                '{default}', storageLimitInput.dataset.default
            );
            storageLimitInput.addEventListener('change', () => saveStorageLimit(user.login, storageLimitInput));
            storageCell.appendChild(storageLimitInput);

            const storageUnit = document.createElement('span');
            storageUnit.className = 'storage-unit';
            storageUnit.textContent = t('mb');
            storageCell.appendChild(storageUnit);

            if (storageUsedMb >= storageLimitMb) {
                storageCell.classList.add('storage-over-quota');
            }
            row.appendChild(storageCell);
            const sessionsCell = document.createElement('td');
            sessionsCell.textContent = user.sessions_count;
            row.appendChild(sessionsCell);
            const tasksCell = document.createElement('td');
            tasksCell.textContent = user.tasks_count || 0;
            row.appendChild(tasksCell);
            const outgoingTokensCell = document.createElement('td');
            outgoingTokensCell.textContent = (user.outgoing_tokens || 0).toLocaleString();
            row.appendChild(outgoingTokensCell);
            const incomingTokensCell = document.createElement('td');
            incomingTokensCell.textContent = (user.incoming_tokens || 0).toLocaleString();
            row.appendChild(incomingTokensCell);
            const messagesCell = document.createElement('td');
            messagesCell.textContent = user.messages_count;
            row.appendChild(messagesCell);
            const filesCell = document.createElement('td');
            filesCell.textContent = user.files_count || 0;
            row.appendChild(filesCell);
            const documentsCell = document.createElement('td');
            documentsCell.textContent = user.documents_count || 0;
            row.appendChild(documentsCell);
            const slmCell = document.createElement('td');
            slmCell.textContent = user.slm_facts_count || 0;
            row.appendChild(slmCell);
            // Column order must match admin.html: Internet requests, Tavily API, then FLAI API.
            const webRequestsCell = document.createElement('td');
            webRequestsCell.textContent = user.web_requests_month || 0;
            webRequestsCell.title = t('internet_requests_hint');
            row.appendChild(webRequestsCell);
            const tavilyCell = document.createElement('td');
            tavilyCell.className = 'tavily-credits';
            tavilyCell.textContent = '…';
            row.appendChild(tavilyCell);
            tavilyCells.set(user.login, tavilyCell);
            const flaiApiCell = document.createElement('td');
            flaiApiCell.textContent = user.api_keys_count ? user.api_keys_count : '-';
            row.appendChild(flaiApiCell);
            if (window.ROOMS && Object.keys(window.ROOMS).length > 0) {
                const camCell = document.createElement('td');
                const camContainer = document.createElement('div');
                camContainer.className = 'camera-checkboxes';
                for (const [code, name] of Object.entries(window.ROOMS)) {
                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.value = code;
                    cb.checked = user.camera_permissions && user.camera_permissions.includes(code);
                    cb.addEventListener('change', () => updateCameraPermissions(user.login));
                    const label = document.createElement('label');
                    label.appendChild(cb);
                    label.appendChild(document.createTextNode(' ' + name));
                    camContainer.appendChild(label);
                }
                camCell.appendChild(camContainer);
                row.appendChild(camCell);
            }
            const actionsCell = document.createElement('td');
            const deleteBtn = document.createElement('button');
            deleteBtn.textContent = t('delete');
            deleteBtn.title = t('delete_user');
            deleteBtn.className = 'delete-user-btn';
            deleteBtn.onclick = () => deleteUser(user.login);
            actionsCell.appendChild(deleteBtn);
            row.appendChild(actionsCell);
            labelUserCardCells(row);
            tbody.appendChild(row);
        });
        if (tavilyCells.size) {
            fetch('/admin/api/users/tavily-usage')
                .then(response => response.json())
                .then(usage => {
                    tavilyCells.forEach((cell, login) => {
                        const state = usage[login];
                        if (!state) {
                            cell.textContent = '-';
                            return;
                        }
                        if (state.status === 'ok') {
                            cell.textContent = state.remaining;
                        } else if (state.status === 'exhausted') {
                            cell.textContent = '0';
                        } else if (state.status === 'no_key') {
                            cell.textContent = '-';
                        } else {
                            cell.textContent = '?';
                            cell.title = state.status === 'invalid' ? t('tavily_invalid_key') : t('tavily_unavailable');
                        }
                    });
                })
                .catch(err => {
                    dlog('Tavily usage load failed:', err);
                    tavilyCells.forEach((cell) => { cell.textContent = '-'; });
                });
        }
    })
    .catch(err => console.error('Error loading users:', err));
}

function saveStorageLimit(login, input) {
    const raw = String(input.value || '').trim();
    let value = null;
    if (raw !== '') {
        value = parseInt(raw, 10);
        if (!Number.isFinite(value) || value < 1) {
            alert(t('storage_limit_invalid'));
            input.value = input.dataset.default;
            return;
        }
    }
    fetchWithCSRF(`/admin/api/users/${login}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ storage_limit_mb: value })
    }).then(response => response.json()).then(result => {
        if (result.status !== 'ok') {
            alert(t('storage_limit_invalid'));
            return;
        }
        if (result.storage_limit_mb === null) {
            loadUsers();
        }
    }).catch(err => {
        console.error('Storage limit update failed', err);
        alert(t('storage_limit_invalid'));
    });
}

function updateUserField(login, field, value) {
    const data = { [field]: value };
    fetchWithCSRF(`/admin/api/users/${login}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(response => response.json()).then(result => {
        if (result.status === 'ok') {
            dlog('Updated', login, field, value);
        } else {
            console.error('Update failed', result);
        }
    }).catch(err => console.error('Error updating user:', err));
}

function updateCameraPermissions(login) {
    const row = document.querySelector(`tr[data-login="${login}"]`);
    if (!row) return;
    const checkboxes = row.querySelectorAll('.camera-checkboxes input[type=checkbox]');
    const permissions = Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);
    fetchWithCSRF(`/admin/api/users/${login}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_permissions: permissions })
    }).then(response => response.json()).then(result => {
        if (result.status !== 'ok') {
            console.error('Update camera permissions failed', result);
        }
    }).catch(err => console.error('Error updating camera permissions:', err));
}

function deleteUser(login) {
    if (confirm(t('delete_user_confirm').replace('{login}', login))) {
        fetchWithCSRF(`/admin/api/users/${login}`, { method: 'DELETE' })
        .then(response => response.json())
        .then(result => {
            if (result.status === 'ok') {
                loadUsers();
            } else {
                alert(t('error') + ': ' + (result.error || t('unknown_error')));
            }
        })
        .catch(err => console.error('Error deleting user:', err));
    }
}

function openPasswordModal(login) {
    document.getElementById('password-user-login').value = login;
    document.getElementById('password-modal').style.display = 'block';
}

function setupModals() {
    const modal = document.getElementById('add-user-modal');
    const passModal = document.getElementById('password-modal');
    const addBtn = document.getElementById('add-user-button');
    const closeSpans = document.querySelectorAll('.modal .close');
    if (!addBtn) {
        console.error('Add user button not found');
        return;
    }
    if (!modal) {
        console.error('Add user modal not found');
        return;
    }
    if (!passModal) {
        console.error('Password modal not found');
        return;
    }
    addBtn.addEventListener('click', () => {
        dlog('Add button clicked');
        modal.style.display = 'block';
    });
    closeSpans.forEach(span => {
        span.addEventListener('click', () => {
            modal.style.display = 'none';
            passModal.style.display = 'none';
        });
    });
    window.addEventListener('click', (event) => {
        if (event.target == modal) modal.style.display = 'none';
        if (event.target == passModal) passModal.style.display = 'none';
    });
    const addForm = document.getElementById('add-user-form');
    if (addForm) {
        addForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const formData = new FormData(e.target);
            const cameraPermissions = Array.from(document.querySelectorAll('#add-user-modal input[name="camera"]:checked')).map(cb => cb.value);
            const data = {
                login: formData.get('login'),
                password: formData.get('password'),
                name: formData.get('name'),
                service_class: parseInt(formData.get('service_class')),
                is_active: formData.get('is_active') === 'on',
                camera_permissions: cameraPermissions.length > 0 ? cameraPermissions : []
            };
            fetchWithCSRF('/admin/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(res => res.json())
            .then(result => {
                if (result.status === 'ok') {
                    modal.style.display = 'none';
                    loadUsers();
                    e.target.reset();
                } else {
                    alert(t('error') + ': ' + (result.error || t('unknown_error')));
                }
            })
            .catch(err => console.error('Error adding user:', err));
        });
    }
    const passForm = document.getElementById('password-form');
    if (passForm) {
        passForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const login = document.getElementById('password-user-login').value;
            const newPassword = document.getElementById('new-user-password').value;
            fetchWithCSRF(`/admin/api/users/${login}/password`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_password: newPassword })
            })
            .then(res => res.json())
            .then(result => {
                if (result.status === 'ok') {
                    passModal.style.display = 'none';
                    document.getElementById('new-user-password').value = '';
                } else {
                    alert(t('error') + ': ' + (result.error || t('unknown_error')));
                }
            })
            .catch(err => console.error('Error changing password:', err));
        });
    }
}

function refreshStats() {
    loadUsers();
    fetch('/admin/api/stats')
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            console.error('Error fetching stats:', data.error);
            return;
        }
        const chatMb = (data.chat_db_size / (1024 * 1024)).toFixed(2);
        const userMb = (data.user_db_size / (1024 * 1024)).toFixed(2);
        const filesMb = (data.files_db_size / (1024 * 1024)).toFixed(2);
        const documentsMb = (data.documents_db_size / (1024 * 1024)).toFixed(2);
        // Update with unit (MB) to keep them visible
        document.getElementById('chat-db-size').textContent = chatMb + ' ' + t('mb');
        document.getElementById('user-db-size').textContent = userMb + ' ' + t('mb');
        document.getElementById('files-db-size').textContent = filesMb + ' ' + t('mb');
        document.getElementById('documents-db-size').textContent = documentsMb + ' ' + t('mb');
    })
    .catch(err => console.error('Error fetching stats:', err));
}

function formatMemMb(mb) {
    if (!mb) return '0';
    return (mb / 1024).toFixed(1);
}

function loadHardware() {
    const tbody = document.getElementById('hardware-tbody');
    if (!tbody) return;
    fetch('/admin/api/hardware', { credentials: 'same-origin' })
    .then(response => response.json())
    .then(data => {
        if (!data || data.error) {
            console.error('Error fetching hardware:', data && data.error);
            return;
        }
        const platform = data.platform || 'cpu';
        const noGpu = platform === 'cpu' || !data.gpu_name;
        const platformLabels = {
            nvidia: 'NVIDIA',
            amd: 'AMD',
            intel: 'Intel',
            cpu: 'CPU'
        };
        const rows = [
            [t('Platform'), platformLabels[platform] || platform],
            [t('GPU'), noGpu ? '—' : data.gpu_name],
            [t('VRAM'), noGpu ? '—' : formatMemMb(data.available_vram_mb) + ' ' + t('GB') + ' ' + t('of') + ' ' + formatMemMb(data.total_vram_mb) + ' ' + t('GB')]
        ];
        if (!data.cpu_count) {
            data.cpu_count = 0;
        }
        rows.push([t('CPU cores'), data.cpu_count]);
        rows.push([t('RAM'), formatMemMb(data.available_ram_mb) + ' ' + t('GB') + ' ' + t('of') + ' ' + formatMemMb(data.total_ram_mb) + ' ' + t('GB')]);
        rows.push([t('CPU'), (data.cpu_name || '—') + (data.cpu_count ? ' · ' + data.cpu_count + ' ' + t('CPU cores') : ''), true]);

        tbody.innerHTML = rows.map(function(row) {
            const nowrap = row[2] ? ' class="nowrap"' : '';
            return '<tr' + nowrap + '><td class="hw-label">' + row[0] + '</td><td class="hw-value">' + row[1] + '</td></tr>';
        }).join('');
    })
    .catch(err => console.error('Error fetching hardware:', err));
}
