// app/static/js/chat-tasks.js
// Tasks (Задачи) panel and task conversation view (v12.6, spec §5).
//
// The whole subsystem runs as low-priority background tasks: one active task
// globally, executed in checkpointed steps, answering clarifying questions
// when asked. This file renders the left-column tab (between Documents and
// Sessions), the task list, the task conversation inside #chat-messages and
// the stop/answer/delete controls. Live updates arrive as SSE `task_update`
// events (events.js dispatch) — no polling.

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

// task_id → task summary {id, title, status, task_class, next_step_index,
// total_steps, created_at, started_at, completed_at, abort_reason}
let tasksData = {};

// The task whose conversation is open in the main window (null = chat mode).
// Exposed on window so the generic send flow (chat-init.js sendMessage) can
// route the input to the task answer.
window.currentTaskId = null;

const TASK_TERMINAL_STATUSES = new Set(['completed', 'failed', 'expired', 'cancelled']);

let taskReloadTimer = null;
let taskOrigPlaceholder = null;

// True while a task conversation is open in the main window. The chat SSE
// render paths (events.js) consult this to keep session streams from
// interleaving into the task conversation — #chat-messages is a single
// container and the task view is its second "mode" (like IS_RELOADING).
function isTaskViewActive() {
    return Boolean(window.currentTaskId);
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

function loadTasksFromServer() {
    return fetch('/api/tasks')
        .then(res => {
            if (!res.ok) {
                throw new Error(`HTTP error ${res.status}`);
            }
            return res.json();
        })
        .then(payload => {
            tasksData = {};
            (payload.tasks || []).forEach(task => {
                tasksData[task.id] = task;
            });
            if (window.currentTaskId && !tasksData[window.currentTaskId]) {
                // The open task was deleted elsewhere — return to the chat.
                closeTaskView(false);
            }
            renderTasksList();
        })
        .catch(err => {
            console.error('Error loading tasks:', err);
        });
}

function renderTasksList() {
    const listEl = document.getElementById('tasks-list');
    if (!listEl) return;

    const tasks = Object.values(tasksData);
    if (tasks.length === 0) {
        listEl.innerHTML = '<div class="tasks-empty">' + escapeHtml(t('task_no_tasks')) + '</div>';
        const countEl = document.getElementById('tasks-count');
        if (countEl) countEl.textContent = '0';
        return;
    }

    let html = '';
    tasks.forEach(task => {
        const isActive = task.id === window.currentTaskId ? 'active' : '';
        const dateStr = task.created_at ? formatFullDateTime(task.created_at) : '';
        const steps = task.total_steps ? ' [' + task.total_steps + ']' : '';
        html += `
<div class="task-item ${isActive}" data-task-id="${escapeHtml(task.id)}">
    <div class="session-content">
        <div class="session-info">
            <div class="session-title">📝 ${escapeHtml(task.title || '')}</div>
            <div class="session-date">📅 ${dateStr}${steps}</div>
            <div class="task-status-line">${escapeHtml(taskStatusLine(task))}</div>
        </div>
        <button class="delete-session-button task-delete-button" title="${t('delete_task')}">🗑️</button>
    </div>
</div>
`;
    });

    listEl.innerHTML = html;
    const countEl = document.getElementById('tasks-count');
    if (countEl) countEl.textContent = tasks.length;
    attachTaskEventHandlers();
}

function attachTaskEventHandlers() {
    document.querySelectorAll('.task-item').forEach(el => {
        el.addEventListener('click', function(e) {
            if (e.target.closest('.task-delete-button')) return;
            const taskId = this.dataset.taskId;
            if (taskId === window.currentTaskId) {
                loadTaskConversation(taskId);
                if (typeof closeMobilePanel === 'function') closeMobilePanel();
                return;
            }
            openTaskView(taskId);
            if (typeof closeMobilePanel === 'function') closeMobilePanel();
        });
    });
    document.querySelectorAll('.task-delete-button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const taskItem = this.closest('.task-item');
            if (!taskItem) return;
            deleteTask(taskItem.dataset.taskId);
        });
    });
}

function taskStatusLine(task) {
    const status = task.status;
    const total = Number(task.total_steps) || 0;
    if (status === 'queued') return '⏳ ' + t('queued');
    if (status === 'active') {
        if (total > 0) {
            const n = Math.min((Number(task.next_step_index) || 0) + 1, total);
            return '▶ ' + formatString(t('task_status_running'), { n: n, m: total });
        }
        // Plan phase: the executor is running (making the plan) but no steps
        // exist yet — never render this as "queued".
        return '▶ ' + t('task_running');
    }
    if (status === 'frozen') return '⏸ ' + t('task_status_frozen');
    if (status === 'awaiting_user') return '⏳ ' + t('task_awaiting_answer');
    if (status === 'completed') return '✔️ ' + t('task_status_completed');
    if (status === 'failed') return '✖️ ' + t('task_status_failed');
    if (status === 'expired') return '⏰ ' + t('task_status_expired');
    if (status === 'cancelled') return '✖️ ' + t('task_status_cancelled');
    return String(status || '');
}

// ---------------------------------------------------------------------------
// Conversation view
// ---------------------------------------------------------------------------

function openTaskView(taskId) {
    if (!taskId) return;
    window.currentTaskId = taskId;
    document.querySelectorAll('.task-item').forEach(item => {
        item.classList.toggle('active', item.dataset.taskId === taskId);
    });
    const container = document.getElementById('chat-messages');
    if (container) {
        container.innerHTML = '';
        if (typeof showMessagesLoadingIndicator === 'function') {
            showMessagesLoadingIndicator();
        }
    }
    setTaskMode(true, tasksData[taskId]);
    loadTaskConversation(taskId);
}

function closeTaskView(silent) {
    if (!window.currentTaskId) return;
    window.currentTaskId = null;
    setTaskMode(false, null);
    if (!silent) {
        // Reload the current chat session so the main window stops showing the
        // task conversation (delete-closes; a session switch calls with
        // silent=true because it loads its own messages).
        const container = document.getElementById('chat-messages');
        if (container) container.innerHTML = '';
        // currentSessionId is a top-level `let` in chat-constants.js — a lexical
        // global, not a window property (window.currentSessionId is undefined).
        if (typeof window.loadMessages === 'function' && currentSessionId) {
            window.loadMessages(currentSessionId).catch(() => {});
        }
        if (typeof updateMessageCount === 'function') updateMessageCount();
    }
}

function loadTaskConversation(taskId) {
    return fetch('/api/tasks/' + encodeURIComponent(taskId) + '/messages')
        .then(res => {
            if (!res.ok) {
                const error = new Error(`HTTP error ${res.status}`);
                error.status = res.status;
                throw error;
            }
            return res.json();
        })
        .then(payload => {
            if (window.currentTaskId !== taskId) return; // switched away meanwhile
            if (payload.task) {
                tasksData[taskId] = Object.assign({}, tasksData[taskId] || {}, payload.task);
            }
            renderTaskConversation(payload);
            setTaskMode(true, tasksData[taskId]);
        })
        .catch(err => {
            if (typeof window.hideMessagesLoadingIndicator === 'function') {
                window.hideMessagesLoadingIndicator();
            }
            // 404 races a pending SSE reload after the task was deleted — the
            // view closes right after, no alert needed.
            if (err.status === 404) {
                console.warn('Task messages gone (deleted?):', taskId);
                return;
            }
            console.error('Error loading task messages:', err);
            alert(t('error') + ': ' + err.message);
        });
}

function scheduleTaskReload(taskId) {
    if (taskReloadTimer) clearTimeout(taskReloadTimer);
    taskReloadTimer = setTimeout(() => {
        taskReloadTimer = null;
        loadTaskConversation(taskId);
    }, 400);
}

function renderTaskConversation(payload) {
    const task = payload.task || {};
    const messages = payload.messages || [];
    const container = document.getElementById('chat-messages');
    if (!container) return;

    // Preserve the scroll position unless the user is at the bottom (new
    // step content then sticks to the bottom like a live chat).
    const nearBottom = typeof isNearBottom === 'function' ? isNearBottom(container) : true;
    const scrollTop = container.scrollTop;

    container.innerHTML = '';

    // Status banner above the conversation (list item may be off-screen).
    const banner = document.createElement('div');
    banner.className = 'task-status-banner';
    banner.textContent = taskStatusLine(task);
    container.appendChild(banner);

    messages.forEach(m => {
        container.appendChild(renderTaskMessage(m));
    });

    if (nearBottom) {
        container.scrollTop = container.scrollHeight;
    } else {
        container.scrollTop = scrollTop;
    }

    const info = document.getElementById('context-info');
    if (info) info.textContent = t('messages') + ': ' + messages.length;
}

function renderTaskMessage(m) {
    const div = document.createElement('div');
    const isUser = m.role === 'user';
    div.className = isUser ? 'user-message' : 'assistant-message bot-message';
    if (m.kind) div.setAttribute('data-kind', m.kind);
    if (m.created_at) div.setAttribute('data-timestamp', m.created_at);
    div.setAttribute('data-raw-text', String(m.content || ''));

    const header = document.createElement('span');
    header.className = 'message-header';
    const ts = m.created_at ? formatFullDateTime(m.created_at) : '';
    header.textContent = '📅 ' + (ts || '');
    div.appendChild(header);

    const content = document.createElement('div');
    content.className = 'message-content';
    content.innerHTML = DOMPurify.sanitize(marked.parse(String(m.content || '')));
    div.appendChild(content);
    return div;
}

// ---------------------------------------------------------------------------
// Task mode (header + input state)
// ---------------------------------------------------------------------------

// active=true shows the «Задача» title and the ■ Stop button and turns the
// message input into the clarifying-question answer box.
function setTaskMode(active, task) {
    const taskTitle = document.getElementById('task-title-text');
    const chatTitle = document.getElementById('chat-title-text');
    const stopBtn = document.getElementById('stop-task-button');
    if (taskTitle) taskTitle.classList.toggle('hidden', !active);
    if (chatTitle) chatTitle.classList.toggle('hidden', active);
    const stopped = !task || !task.status || TASK_TERMINAL_STATUSES.has(task.status);
    if (stopBtn) stopBtn.classList.toggle('hidden', !active || stopped);

    const input = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-button');
    if (!input) return;
    if (!active) {
        input.readOnly = false;
        input.placeholder = taskOrigPlaceholder || t('enter_message_or_file');
        if (sendBtn) sendBtn.disabled = false;
        return;
    }
    if (taskOrigPlaceholder === null) taskOrigPlaceholder = input.placeholder;
    const awaiting = task.status === 'awaiting_user';
    input.readOnly = !awaiting;
    input.placeholder = awaiting ? t('task_answer_placeholder') : taskStatusLine(task);
    // The send button doubles as the answer submit; a non-awaiting task has
    // no input to accept (chat-init.js sendMessage routes to sendTaskAnswer).
    if (sendBtn) sendBtn.disabled = !awaiting;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

// Called from chat-init.js sendMessage when a task is open: the input is the
// answer to the awaiting clarifying question (server validates with 409).
function sendTaskAnswer() {
    if (!window.currentTaskId) return;
    const task = tasksData[window.currentTaskId];
    if (!task || task.status !== 'awaiting_user') return;
    const input = document.getElementById('message-input');
    const text = input ? input.value.trim() : '';
    if (!text) return;
    input.value = '';
    fetchWithCSRF('/api/tasks/' + encodeURIComponent(window.currentTaskId) + '/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: text }),
    })
        .then(res => res.json().catch(() => ({})))
        .then(data => {
            if (data.error) {
                // Restore the answer — a 409 (stale awaiting state) must not
                // wipe a long typed answer.
                if (input) input.value = text;
                alert(data.error);
                return;
            }
            scheduleTaskReload(window.currentTaskId);
        })
        .catch(err => {
            if (input) input.value = text;
            alert(t('error') + ': ' + err.message);
        });
}

function stopCurrentTask() {
    if (!window.currentTaskId) return;
    fetchWithCSRF('/api/tasks/' + encodeURIComponent(window.currentTaskId) + '/stop', { method: 'POST' })
        .then(res => res.json().catch(() => ({})))
        .then(data => {
            if (data.error) {
                alert(data.error);
                return;
            }
            loadTasksFromServer();
            if (window.currentTaskId) scheduleTaskReload(window.currentTaskId);
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

function deleteTask(taskId) {
    const task = tasksData[taskId];
    if (!task) return;
    const dateStr = task.created_at ? formatFullDateTime(task.created_at) : '';
    const confirmMessage = formatString(t('delete_task_confirm'), {
        title: (task.title || '').slice(0, 60),
        date: dateStr,
    });
    if (!confirm(confirmMessage)) return;

    fetchWithCSRF('/api/tasks/' + encodeURIComponent(taskId) + '/delete', { method: 'POST' })
        .then(res => res.json().catch(() => ({})))
        .then(data => {
            if (data.error) {
                alert(data.error);
                return;
            }
            delete tasksData[taskId];
            if (window.currentTaskId === taskId) {
                closeTaskView(false);
            }
            renderTasksList();
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

// ---------------------------------------------------------------------------
// New task dialog
// ---------------------------------------------------------------------------

function openNewTaskModal() {
    const modal = document.getElementById('new-task-modal');
    const input = document.getElementById('new-task-request');
    const err = document.getElementById('new-task-error');
    if (err) err.hidden = true;
    if (input) input.value = '';
    if (modal) modal.hidden = false;
    document.addEventListener('keydown', onNewTaskModalKeydown);
    if (input && typeof input.focus === 'function') input.focus();
}

function closeNewTaskModal() {
    const modal = document.getElementById('new-task-modal');
    if (modal) modal.hidden = true;
    document.removeEventListener('keydown', onNewTaskModalKeydown);
}

// Escape closes the dialog (Enter intentionally does NOT submit — the request
// textarea is multiline).
function onNewTaskModalKeydown(e) {
    if (e.key === 'Escape') closeNewTaskModal();
}

function submitNewTask() {
    const input = document.getElementById('new-task-request');
    const err = document.getElementById('new-task-error');
    const requestText = input ? input.value.trim() : '';
    if (!requestText) {
        if (err) {
            err.textContent = '⚠️ ' + t('empty_message');
            err.hidden = false;
        }
        return;
    }
    fetchWithCSRF('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request: requestText }),
    })
        .then(res => res.json().catch(() => ({})))
        .then(data => {
            if (data.error) {
                if (err) {
                    err.textContent = data.error;
                    err.hidden = false;
                }
                return;
            }
            closeNewTaskModal();
            if (data.task && data.task.id) {
                tasksData[data.task.id] = data.task;
                renderTasksList();
                openTaskView(data.task.id);
            } else {
                loadTasksFromServer();
            }
        })
        .catch(e => {
            if (err) {
                err.textContent = t('error') + ': ' + e.message;
                err.hidden = false;
            }
        });
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

// Dispatched by events.js handleEvent for `task_update` events.
// data: {task_id, status, title, next_step_index, total_steps, abort_reason}
function onTaskUpdate(data) {
    if (!data || !data.task_id) return;
    if (data.status === 'deleted') {
        delete tasksData[data.task_id];
        if (window.currentTaskId === data.task_id) {
            closeTaskView(false);
        }
        renderTasksList();
        return;
    }
    const prev = tasksData[data.task_id] || {};
    tasksData[data.task_id] = Object.assign({}, prev, {
        id: data.task_id,
        status: data.status,
        title: data.title || prev.title || '',
        next_step_index: data.next_step_index,
        total_steps: data.total_steps,
        abort_reason: data.abort_reason,
    });
    renderTasksList();
    if (window.currentTaskId === data.task_id) {
        scheduleTaskReload(data.task_id);
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function initTasksView() {
    const newTaskBtn = document.getElementById('new-task-button');
    if (newTaskBtn) {
        newTaskBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            openNewTaskModal();
        });
    }
    const modal = document.getElementById('new-task-modal');
    if (modal) {
        modal.querySelectorAll('[data-new-task-close]').forEach(el => {
            el.addEventListener('click', closeNewTaskModal);
        });
        const cancelBtn = document.getElementById('new-task-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', closeNewTaskModal);
        const submitBtn = document.getElementById('new-task-submit');
        if (submitBtn) submitBtn.addEventListener('click', submitNewTask);
    }
    const stopBtn = document.getElementById('stop-task-button');
    if (stopBtn) {
        stopBtn.addEventListener('click', stopCurrentTask);
    }
    // Initial load: fills the tab counter even before the first switch and
    // restores the open-task state after a page reload is not needed (views
    // reload per switch), but the counter must be right immediately.
    loadTasksFromServer();
}

document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('tasks-list')) {
        initTasksView();
    }
});