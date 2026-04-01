// app/static/js/admin.js
// Admin panel JavaScript - handles user management, translations, and page-specific setup

let currentSortField = null;
let currentSortDirection = 'asc';

document.addEventListener('DOMContentLoaded', function() {
    console.log('Admin page loaded');
    // Add a class to body to identify admin page for CSS overrides
    document.body.classList.add('admin-page');

    loadUsers();
    setupModals();
    setupSortableHeaders();
    setInterval(refreshStats, 30000);
});

// Get CSRF token from meta tag
function getCSRFToken() {
    const token = document.querySelector('meta[name="csrf-token"]');
    return token ? token.getAttribute('content') : '';
}

// Fetch wrapper with CSRF token for POST/PUT/DELETE requests
function fetchWithCSRF(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    
    // Add CSRF token for state-changing requests
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
        const headers = options.headers || {};
        if (!headers['X-CSRFToken'] && !headers['X-CSRF-TOKEN']) {
            headers['X-CSRFToken'] = getCSRFToken();
        }
        options.headers = headers;
    }
    
    return fetch(url, options);
}

function t(key) {
    if (!(key in window.TRANSLATIONS)) {
        console.warn('Missing translation key:', key);
        return key;
    }
    return window.TRANSLATIONS[key];
}

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
        console.log('Users loaded:', users);
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
            const sessionsCell = document.createElement('td');
            sessionsCell.textContent = user.sessions_count;
            row.appendChild(sessionsCell);
            const messagesCell = document.createElement('td');
            messagesCell.textContent = user.messages_count;
            row.appendChild(messagesCell);
            const filesCell = document.createElement('td');
            filesCell.textContent = user.files_count || 0;
            row.appendChild(filesCell);
            const documentsCell = document.createElement('td');
            documentsCell.textContent = user.documents_count || 0;
            row.appendChild(documentsCell);
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
            tbody.appendChild(row);
        });
    })
    .catch(err => console.error('Error loading users:', err));
}

function updateUserField(login, field, value) {
    const data = { [field]: value };
    fetchWithCSRF(`/admin/api/users/${login}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(response => response.json()).then(result => {
        if (result.status === 'ok') {
            console.log('Updated', login, field, value);
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
        console.log('Add button clicked');
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