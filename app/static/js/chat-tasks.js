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

// Live plan-phase stages (SSE task_stage): task_id → {stage, since}.
// The banner above the open task renders the stage with a ticking counter
// while the executor loads the reasoning model / makes the plan.
let taskStages = {};

const TASK_TERMINAL_STATUSES = new Set(['completed', 'failed', 'expired', 'cancelled']);

let taskReloadTimer = null;
// Queue positions are global — any task entering or leaving the queue shifts
// the numbers of the others — so a status event re-reads the list (debounced)
// instead of trusting the per-task patch alone.
let tasksListReloadTimer = null;
let taskOrigPlaceholder = null;

// True when the Tasks tab is open in the main window but no task is selected:
// the empty task state (title «Задача», no input) instead of the chat.
let tasksViewEmpty = false;

// The last task opened in the main window. Unlike the task VIEW (which is
// closed when the tab is left so the chat can be restored into the shared
// #chat-messages container), this id survives the tab switch — and a page
// reload, mirroring how the chat keeps its last session open.
let lastTaskId = null;

function rememberTaskId(taskId) {
    lastTaskId = taskId;
    const login = window.CURRENT_USER_LOGIN;
    if (login && taskId) localStorage.setItem(`last_task_${login}`, taskId);
}

function readRememberedTaskId() {
    if (lastTaskId) return lastTaskId;
    const login = window.CURRENT_USER_LOGIN;
    if (!login) return null;
    return localStorage.getItem(`last_task_${login}`) || null;
}

function forgetTaskId() {
    lastTaskId = null;
    const login = window.CURRENT_USER_LOGIN;
    if (login) localStorage.removeItem(`last_task_${login}`);
}

// Last rendered conversation payload (JSON string) — a re-delivered identical
// payload (e.g. a redundant SSE reload) must not rebuild the DOM (flicker).
let lastTaskPayloadJson = '';

// The awaiting-answer live status (chat-style pulsing element + elapsed
// seconds) — one per task view, started on entering awaiting_user.
let taskAwaitTimer = null;
let taskAwaitStartedAt = null;

// The plan-phase stage timer (same ticking pattern) — one per task view.
let taskStageTimer = null;
let taskStageStartedAt = null;
let taskStageTimerTaskId = null;

// True while the Tasks tab owns the main window: a task conversation is
// open OR the empty task state is showing. The chat render paths consult
// this to keep session streams and message reloads from interleaving into
// the task window — #chat-messages is a single container and the task view
// is its second "mode" (like IS_RELOADING). When the view closes
// (leaveTasksView/closeTaskView) both flags are cleared BEFORE the chat
// reload is requested, so the restore path renders normally.
function isTaskViewActive() {
    return Boolean(window.currentTaskId) || tasksViewEmpty;
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
                // The open task was deleted elsewhere — show another one (or
                // the empty state), never the chat session window.
                replaceClosedTask();
            }
            if (readRememberedTaskId() && !tasksData[readRememberedTaskId()]) {
                forgetTaskId(); // deleted: a reload must not try to restore it
            }
            renderTasksList();
            return true;
        })
        .catch(err => {
            console.error('Error loading tasks:', err);
            return false;
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
        const title = task.title || t('task_status_draft');
        // Token totals (▲outgoing requests ▼incoming answers) like session
        // cards, from summed step usage.
        const totals = task.token_totals || {};
        const tokensHTML = sessionTokenStatsHTML(totals.prompt_tokens, totals.completion_tokens);
        const secs = Number(task.exec_seconds) || 0;
        const execHTML = secs >= 0.1 ? ' <span class="text-muted">⏱️ ' + secs.toFixed(0) + t('seconds_suffix') + '</span>' : '';
        html += `
<div class="task-item ${isActive}" data-task-id="${escapeHtml(task.id)}">
    <div class="session-content">
        <div class="session-info">
            <div class="session-title">${taskStatusIcon(task)}📝 ${escapeHtml(title)}</div>
            <div class="session-date">📅 ${dateStr}${steps}${tokensHTML}</div>
            <div class="task-status-line">${escapeHtml(taskStatusLine(task))}${execHTML}</div>
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

function taskStatusIcon(task) {
    // List badge before the title, mirroring the session cards: a blinking
    // lightning while the executor runs the task, a blinking question mark
    // while it awaits the user's answer, an hourglass with the queue
    // position while it waits its turn. Terminal states carry no badge —
    // the small status line below the title already names them.
    const status = task.status;
    if (status === 'active') {
        return '<span class="session-status-icon processing blink" title="' + escapeHtml(t('task_running')) + '">⚡</span>';
    }
    if (status === 'awaiting_user') {
        return '<span class="session-status-icon awaiting blink" title="' + escapeHtml(t('task_awaiting_answer')) + '">❓</span>';
    }
    if (status === 'queued') {
        const pos = Number(task.queue_position) || 0;
        const label = pos > 0 ? t('queued') + ' (#' + pos + ')' : t('queued');
        return '<span class="session-status-icon queued blink" title="' + escapeHtml(label) + '">⏳' + (pos > 0 ? ' ' + pos : '') + '</span>';
    }
    return '';
}

function taskStatusLine(task) {
    const status = task.status;
    const total = Number(task.total_steps) || 0;
    if (status === 'draft') return '📝 ' + t('task_status_draft');
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
    if (status === 'frozen') {
        // Whisper is transcribing an attached request/answer audio: «frozen»
        // alone («Приостановлено») reads like the chat paused the task.
        if (task.abort_reason === 'audio_transcript') return '🎤 ' + t('task_status_transcribing');
        return '⏸ ' + t('task_status_frozen');
    }
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
    tasksViewEmpty = false;
    window.currentTaskId = taskId;
    lastTaskPayloadJson = '';
    rememberTaskId(taskId);
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

// The Tasks tab opened with no task selected: reopen the last task the user
// had (tab switch or page reload), or create the first draft task when there
// are none (chat parity: the chat never shows an empty session list).
function restoreTasksView() {
    const remembered = readRememberedTaskId();
    // Claim the window immediately (same flag the empty state sets) so the
    // chat's async initial load cannot paint its session over it, then decide
    // once the list has arrived.
    tasksViewEmpty = true;
    lastTaskPayloadJson = '';
    setTaskMode(true, null);
    const container = document.getElementById('chat-messages');
    if (container) {
        container.innerHTML = '';
        if (typeof showMessagesLoadingIndicator === 'function') {
            showMessagesLoadingIndicator();
        }
    }
    loadTasksFromServer().then(ok => {
        if (remembered && tasksData[remembered]) {
            openTaskView(remembered);
            return;
        }
        const ids = Object.keys(tasksData);
        if (ok && ids.length) {
            // The remembered id is gone but other tasks exist (or was never
            // set on this device): open the newest/first, never create a
            // duplicate.
            if (remembered) forgetTaskId();
            openTaskView(ids[0]);
            return;
        }
        if (ok) {
            // Zero tasks: create the first one (chat parity). The window
            // remains claimed while the request is in flight.
            ensureFirstTask();
            return;
        }
        // The list could not be fetched — creating a task now could duplicate
        // one; show the empty state instead.
        tasksViewEmpty = false;
        enterTasksEmptyState();
    });
}

// The main window shows the task empty state (title «Задача», hint, no input)
// instead of the chat. Only reached when a task cannot be created or the list
// cannot be loaded — the normal empty list creates its first task.
function enterTasksEmptyState() {
    if (window.currentTaskId) {
        closeTaskView(true); // silent: the empty state replaces the view
        return;
    }
    tasksViewEmpty = true;
    lastTaskPayloadJson = '';
    const container = document.getElementById('chat-messages');
    if (container) container.innerHTML = '';
    const info = document.getElementById('context-info');
    if (info) info.textContent = '';
    setTaskMode(true, null);
    if (container) {
        const hint = document.createElement('div');
        hint.className = 'task-empty-state';
        hint.textContent = t('task_empty_state');
        container.appendChild(hint);
    }
}

// The Tasks tab closed (Sessions/Documents opened): restore the chat window.
function leaveTasksView() {
    stopAwaitTimer();
    tasksViewEmpty = false;
    lastTaskPayloadJson = '';
    if (window.currentTaskId) {
        closeTaskView(false);
        return;
    }
    // Empty state was showing — restore the current chat conversation.
    const container = document.getElementById('chat-messages');
    if (container) container.innerHTML = '';
    setTaskMode(false, null);
    // currentSessionId is a top-level `let` in chat-constants.js — a lexical
    // global, not a window property (window.currentSessionId is undefined).
    if (typeof window.loadMessages === 'function' && currentSessionId) {
        window.loadMessages(currentSessionId).catch(() => {});
    }
    if (typeof updateMessageCount === 'function') updateMessageCount();
}

function closeTaskView(silent) {
    if (!window.currentTaskId) return;
    stopAwaitTimer();
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

// The open task is gone (deleted here or in another tab): open the next task
// of the list, or create the first one. The main window must stay in the Tasks
// view — falling back to the chat session window there was the bug (user
// request).
function replaceClosedTask() {
    closeTaskView(true);
    const remaining = Object.values(tasksData);
    if (remaining.length) {
        openTaskView(remaining[0].id);
    } else {
        ensureFirstTask();
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

function scheduleTasksListReload() {
    if (tasksListReloadTimer) clearTimeout(tasksListReloadTimer);
    tasksListReloadTimer = setTimeout(() => {
        tasksListReloadTimer = null;
        loadTasksFromServer();
    }, 400);
}

function renderTaskConversation(payload) {
    const task = payload.task || {};
    const messages = payload.messages || [];
    const container = document.getElementById('chat-messages');
    if (!container) return;

    // Identical payload → identical DOM: skip the rebuild entirely (a
    // redundant SSE-triggered reload must not flicker the window).
    const payloadJson = JSON.stringify([task.status, task.next_step_index, messages]);
    if (payloadJson === lastTaskPayloadJson) {
        setTaskMode(true, task);
        return;
    }
    lastTaskPayloadJson = payloadJson;

    // Preserve the scroll position unless the user is at the bottom (new
    // step content then sticks to the bottom like a live chat).
    const nearBottom = typeof isNearBottom === 'function' ? isNearBottom(container) : true;
    const scrollTop = container.scrollTop;

    container.innerHTML = '';

    // Status banner above the conversation. Every live (non-terminal) status
    // uses the chat-style pulsing look; awaiting also ticks elapsed seconds.
    const banner = document.createElement('div');
    const live = ['queued', 'active', 'frozen', 'awaiting_user'].includes(task.status);
    if (live) {
        banner.className = 'task-await-banner';
        const stage = (task.status === 'active' && !(Number(task.total_steps) || 0)) ? taskStages[task.id] : null;
        if (stage) {
            // Plan phase progress: model load / planning, ticking per second.
            startStageTimer(task.id, banner);
        } else if (task.status === 'awaiting_user') {
            startAwaitTimer(task.id, banner);
        } else {
            stopAwaitTimer();
            banner.textContent = taskStatusLine(task);
        }
    } else {
        stopAwaitTimer();
        banner.className = 'task-status-banner';
        banner.textContent = taskStatusLine(task);
    }
    container.appendChild(banner);

    messages.forEach(m => {
        container.appendChild(renderTaskMessage(m, task));
    });

    if (nearBottom) {
        container.scrollTop = container.scrollHeight;
    } else {
        container.scrollTop = scrollTop;
    }

    const info = document.getElementById('context-info');
    if (info) info.textContent = t('messages') + ': ' + messages.length;
}

// Chat-style "waiting for your answer" status: pulsing element + a per-second
// elapsed counter («⏳ Ожидаю ответа (5 с)...»), like the chat stage labels.
function startAwaitTimer(taskId, el) {
    stopAwaitTimer();
    taskAwaitStartedAt = Date.now();
    const base = '⏳ ' + t('task_awaiting_answer');
    const suffix = t('stage_elapsed_s');
    const paint = () => {
        const secs = Math.floor((Date.now() - taskAwaitStartedAt) / 1000);
        el.textContent = secs >= 1 ? base + ' (' + secs + suffix + ')' : base;
    };
    paint();
    taskAwaitTimer = setInterval(() => {
        if (!document.body.contains(el)) {
            stopAwaitTimer();
            return;
        }
        paint();
    }, 1000);
    // Keep ticking only while this task is open.
    if (window.currentTaskId !== taskId) stopAwaitTimer();
}

function stopAwaitTimer() {
    if (taskAwaitTimer) {
        clearInterval(taskAwaitTimer);
        taskAwaitTimer = null;
    }
    taskAwaitStartedAt = null;
}

// Live plan-phase stage («🧠 Загружаю модель рассуждений... (5 с)» →
// «🧠 Составляю план... (12 с)»): the SSE task_stage event re-paints the
// banner without a server round-trip, the timer ticks the elapsed seconds.
function startStageTimer(taskId, el) {
    stopStageTimer();
    const stage = taskStages[taskId];
    if (!stage) return;
    taskStageStartedAt = stage.since || Date.now();
    taskStageTimerTaskId = taskId;
    const paint = () => {
        const s = taskStages[taskId];
        if (!s) { stopStageTimer(); return; }
        const label = (typeof getStageLabel === 'function' ? getStageLabel(s.stage) : s.stage) || '';
        const secs = Math.floor((Date.now() - taskStageStartedAt) / 1000);
        el.textContent = '▶ ' + label + (secs >= 1 ? ' (' + secs + t('stage_elapsed_s') + ')' : '');
    };
    paint();
    taskStageTimer = setInterval(() => {
        if (!document.body.contains(el)) {
            stopStageTimer();
            return;
        }
        paint();
    }, 1000);
    if (window.currentTaskId !== taskId) stopStageTimer();
}

function stopStageTimer() {
    if (taskStageTimer) {
        clearInterval(taskStageTimer);
        taskStageTimer = null;
    }
    taskStageStartedAt = null;
    taskStageTimerTaskId = null;
}

// Dispatched by events.js handleEvent for `task_stage` events (plan-phase
// progress). data: {task_id, stage} — an empty stage clears the line.
function onTaskStage(data) {
    if (!data || !data.task_id) return;
    if (data.stage) {
        taskStages[data.task_id] = { stage: data.stage, since: Date.now() };
    } else {
        delete taskStages[data.task_id];
    }
    if (window.currentTaskId !== data.task_id) return;
    const banner = document.querySelector('.task-await-banner');
    if (!banner) return;
    const stage = taskStages[data.task_id];
    if (stage) {
        startStageTimer(data.task_id, banner);
    } else {
        stopStageTimer();
    }
}

function renderTaskMessage(m, task) {
    const div = document.createElement('div');
    const isUser = m.role === 'user';
    div.className = isUser ? 'user-message' : 'assistant-message bot-message';
    if (m.kind) div.setAttribute('data-kind', m.kind);
    if (m.created_at) div.setAttribute('data-timestamp', m.created_at);
    div.setAttribute('data-raw-text', String(m.content || ''));

    // Chat-style header: date | model | ⏱️ duration | 🔢 tokens | 🚀 tps,
    // plus 🤖 style emoji and the 🗣️/📋 buttons — the same shape as the chat's
    // displayMessage header (user request: the task view must look like chat).
    // Each field stands on its own: a step handled by a tool (calculate/search)
    // has no model tokens, and the final summary has tokens with no separate
    // duration column — neither may hide the other.
    const header = document.createElement('span');
    header.className = 'message-header';
    const ts = m.created_at ? formatFullDateTime(m.created_at) : '';
    header.textContent = '📅 ' + (ts || '');
    if (m.model_name) {
        const shortModel = (m.model_name.split('/').pop() || m.model_name).replace(/\.gguf$/i, '');
        header.innerHTML += ' <span class="text-muted">| ' + (getModelEmoji(m.model_type || 'reasoning') || '') + escapeHtml(shortModel) + '</span>';
    }
    const secs = Number(m.exec_seconds) || 0;
    const usage = (!isUser && m.usage) || {};
    const promptTokens = Number(usage.prompt_tokens) || 0;
    const completionTokens = Number(usage.completion_tokens) || 0;
    const hasTokens = promptTokens > 0 || completionTokens > 0;
    if (secs >= 0.1) {
        header.innerHTML += ' <span class="text-muted">| ⏱️ ' + secs.toFixed(1) + t('seconds_suffix') + ' |</span>';
    }
    if (hasTokens) {
        header.innerHTML += tokenStatsHTML(promptTokens, completionTokens);
    }
    if (hasTokens && secs >= 0.1) {
        const tps = (completionTokens / secs).toFixed(1);
        header.innerHTML += ' <span class="text-muted">🚀 ' + tps + t('tps_suffix') + ' |</span>';
    }
    if (!isUser && m.model_name && task && task.response_style) {
        const styleEmoji = getResponseStyleEmoji(task.response_style);
        if (styleEmoji) {
            const styleSpan = document.createElement('span');
            styleSpan.className = 'response-style-indicator';
            styleSpan.textContent = styleEmoji;
            header.appendChild(styleSpan);
        }
    }

    // TTS + copy buttons on EVERY message with a body, user requests included
    // (user request: date, TTS (speak) and copy buttons everywhere).
    if (String(m.content || '').trim()) {
        const ttsButton = document.createElement('button');
        ttsButton.className = 'tts-button';
        ttsButton.title = t('speak');
        ttsButton.textContent = '🗣️';
        ttsButton.addEventListener('click', (e) => {
            e.preventDefault();
            if (typeof window.playTTS === 'function') window.playTTS(ttsButton, div);
        });
        header.appendChild(ttsButton);
        const copyButton = document.createElement('button');
        copyButton.className = 'copy-message-button';
        copyButton.title = t('copy_text');
        copyButton.textContent = '📋';
        copyButton.addEventListener('click', async (e) => {
            e.preventDefault();
            const success = await copyToClipboard(div.dataset.rawText);
            const originalHTML = copyButton.innerHTML;
            const originalTitle = copyButton.title;
            copyButton.innerHTML = success ? '✓' : '✗';
            copyButton.title = t(success ? 'copied' : 'copy_failed');
            setTimeout(() => {
                copyButton.innerHTML = originalHTML;
                copyButton.title = originalTitle;
            }, 2000);
        });
        header.appendChild(copyButton);
    }
    div.appendChild(header);

    // Service lines («План из N шагов:», «Шаг N из M:») render as their own
    // italic muted line with an emoji, separate from the message body.
    const content = document.createElement('div');
    content.className = 'message-content';
    let body = String(m.content || '');
    const serviceLine = splitServiceLine(m.kind, body);
    if (serviceLine) {
        body = serviceLine.rest;
        const svc = document.createElement('div');
        svc.className = 'task-msg-service';
        svc.textContent = serviceLine.text;
        div.appendChild(svc);
        // A tool-produced step names its tool on its own italic line right
        // below the step line (same font). The server sends the localized
        // badge and prefixes the body with it — strip that line before
        // rendering the body.
        if (m.badge) {
            const nl = body.indexOf('\n');
            const firstLine = (nl === -1 ? body : body.slice(0, nl)).trim();
            if (firstLine === m.badge) {
                body = nl === -1 ? '' : body.slice(nl + 1);
            }
            const toolSvc = document.createElement('div');
            toolSvc.className = 'task-msg-service';
            toolSvc.textContent = m.badge;
            div.appendChild(toolSvc);
        }
    }
    content.innerHTML = DOMPurify.sanitize(marked.parse(body));
    div.appendChild(content);

    // Attached files ride the request as ready localized lines with one file
    // name per line (the server joins them) — each renders as a service line,
    // the same font as the plan line; the 📎 marks the block like 🗒️/⚙️ mark
    // the plan and step lines.
    ['attached_images', 'attached_documents', 'attached_audio'].forEach((key) => {
        if (!m[key]) return;
        const attached = document.createElement('div');
        attached.className = 'task-msg-service';
        attached.textContent = '📎 ' + m[key];
        div.appendChild(attached);
    });

    // The transcript of an attached audio (🎤, regular font): the chat shows it
    // as its own message; here it renders below the attachment line. The
    // italic service line would be unreadable for a long transcript.
    (Array.isArray(m.audio_transcripts) ? m.audio_transcripts : []).forEach((block) => {
        if (!block || !block.text) return;
        const tr = document.createElement('div');
        tr.className = 'task-msg-transcript';
        tr.textContent = (block.label || '🎤') + ' ' + block.text;
        div.appendChild(tr);
    });

    // Media refs (camera snapshots, generated images/videos) ride the step's
    // extra — render chat-style image blocks after the text body. The request
    // message carries ALL its attached images in media_all; steps carry one.
    const mediaList = (m.extra && Array.isArray(m.extra.media_all) && m.extra.media_all.length
        ? m.extra.media_all
        : (m.extra && m.extra.media ? [m.extra.media] : []));
    mediaList.forEach(function(media) {
        if (media.file_path && media.file_type && String(media.file_type).startsWith('image/')) {
            const imgContainer = document.createElement('div');
            imgContainer.className = 'image-container';
            const img = document.createElement('img');
            img.src = '/api/files/' + media.file_path;
            img.loading = 'lazy';
            img.className = 'attached-image';
            img.alt = media.file_name || 'image';
            img.title = t('click_to_enlarge');
            img.onclick = function() { openImageModal(img.src, media.file_name || t('image')); };
            imgContainer.appendChild(img);
            div.appendChild(imgContainer);
        } else if (media.file_path && media.file_type && String(media.file_type).startsWith('video/')) {
            const video = document.createElement('video');
            video.controls = true;
            video.preload = 'metadata';
            video.loading = 'lazy';
            video.src = '/api/files/' + media.file_path;
            video.style.maxWidth = '100%';
            video.style.borderRadius = '8px';
            div.appendChild(video);
        }
    });
    return div;
}

// Pull the leading service header out of the message body: the server
// prefixes the plan as «План из N шагов:» (kind=plan) and each step as
// «Шаг N из M:» (direct/subtask) — always the first content line, in the
// user's language. The split therefore keys on the SHAPE (a first line
// ending with a colon), never on the wording, so it survives localization.
function splitServiceLine(kind, body) {
    if (kind !== 'plan' && kind !== 'direct' && kind !== 'subtask') return null;
    const nl = body.indexOf('\n');
    const first = (nl === -1 ? body : body.slice(0, nl)).trim();
    if (!first.endsWith(':')) return null;
    const rest = nl === -1 ? '' : body.slice(nl + 1);
    return { text: (kind === 'plan' ? '🗒️ ' : '⚙️ ') + first, rest };
}

// ---------------------------------------------------------------------------
// Task mode (header + input state)
// ---------------------------------------------------------------------------

// active=true shows the «Задача» title and the ■ Stop button and turns the
// message input into the clarifying-question answer box. task=null is the
// empty tasks view: title shown, input hidden.
function setTaskMode(active, task) {
    const taskTitle = document.getElementById('task-title-text');
    const chatTitle = document.getElementById('chat-title-text');
    const stopBtn = document.getElementById('stop-task-button');
    const retryBtn = document.getElementById('retry-task-button');
    if (taskTitle) taskTitle.classList.toggle('hidden', !active);
    if (chatTitle) chatTitle.classList.toggle('hidden', active);
    const stopped = !task || !task.status || TASK_TERMINAL_STATUSES.has(task.status) || task.status === 'draft';
    if (stopBtn) stopBtn.style.display = active && !stopped ? 'inline-flex' : 'none';
    // The ■ replaces the 💾 in the header row while the task is processing
    // (the chat's own stream does the same swap: events.js
    // _showHeaderCancelButton/_hideHeaderCancelButton) — two buttons of the
    // same size stacked in that slot would break the row on mobile.
    const saveBtn = document.getElementById('save-chat-button');
    if (saveBtn) saveBtn.style.display = active && !stopped ? 'none' : '';
    // Retry button visible on failed/expired/cancelled terminal tasks.
    const canRetry = task && TASK_TERMINAL_STATUSES.has(task.status) && task.status !== 'completed';
    if (retryBtn) retryBtn.style.display = active && canRetry ? 'inline-flex' : 'none';

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
    if (!task) {
        // Empty tasks view: no input accepted (a request goes through «+»).
        input.readOnly = true;
        input.placeholder = t('task_empty_state');
        if (sendBtn) sendBtn.disabled = true;
        return;
    }
    const awaiting = task.status === 'awaiting_user';
    const draft = task.status === 'draft';
    input.readOnly = !awaiting && !draft;
    input.placeholder = draft
        ? t('task_draft_placeholder')
        : awaiting
            ? t('task_answer_placeholder')
            : taskStatusLine(task);
    // The send button doubles as the answer submit (and the draft request
    // submit); a running task has no input to accept.
    if (sendBtn) sendBtn.disabled = !awaiting && !draft;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

// Documents attached to a task = the shared Documents-panel selection at
// submit time (the same #rlm-docs multi-select the chat uses). An empty list
// means "search all of the user's documents".
function taskDocIds() {
    const docsSelect = document.getElementById('rlm-docs');
    return docsSelect ? Array.from(docsSelect.selectedOptions).map(o => o.value) : [];
}

// The 🔬 Deep Analysis toggle rides the submit: the planner is told to
// include an rlm step over the attached documents.
function taskDeepAnalysis() {
    const toggle = document.getElementById('rlm-toggle');
    return !!(toggle && toggle.checked);
}

// Images attached in the task view (the shared chat chips): every queued
// image rides the submit as an "images" form part — the vision capability
// analyzes them (v12.6).
function taskImageFiles() {
    return (typeof attachedFiles !== 'undefined' ? attachedFiles : []).filter(isImageFile);
}

// Documents attached in the task view (the same chips): everything that is
// neither an image nor a voice recording. They ride the submit as "files"
// form parts and become FULL user documents server-side (Documents parity:
// saved + indexed, ids go into the task's doc_ids), so the task searches
// exactly them. The first non-image file may sit in the legacy single slot —
// without it a lone attached document was dropped silently.
function taskDocumentFiles() {
    const files = (typeof attachedFiles !== 'undefined' ? attachedFiles : []).filter(isDocumentFile);
    if (typeof attachedFile !== 'undefined' && attachedFile && isDocumentFile(attachedFile)) {
        files.unshift(attachedFile);
    }
    return files;
}

// Audio attached in the task view (chat parity: the same chips, the paperclip
// and the microphone): voice recordings, audio files and video containers
// (chat transcribes the audio track of a video too). They ride the submit as
// "audio" form parts and are transcribed server-side into the task's context —
// never into the Documents panel. The microphone blob may sit in the legacy
// single slot (a lone recording) or beside an image in attachedVoiceBlob —
// both must reach the task.
function taskAudioFiles() {
    const files = (typeof attachedFiles !== 'undefined' ? attachedFiles : []).filter(isAudioTypeFile);
    if (typeof attachedFile !== 'undefined' && attachedFile && isAudioTypeFile(attachedFile)) {
        files.unshift(attachedFile);
    }
    if (typeof attachedVoiceBlob !== 'undefined' && attachedVoiceBlob && isAudioTypeFile(attachedVoiceBlob)) {
        files.unshift(attachedVoiceBlob);
    }
    return files;
}

// The recorder flagged the chat as transcribing (chat-recording.js); a task
// reports its own progress through task events, so the chat flag must not
// linger and the recording must not ride the NEXT submit again.
function clearTaskVoiceState() {
    if (typeof isVoiceRecorded !== 'undefined') isVoiceRecorded = false;
    if (typeof attachedVoiceBlob !== 'undefined') attachedVoiceBlob = null;
    if (typeof setLocalTranscribing === 'function' && typeof currentSessionId !== 'undefined' && currentSessionId) {
        setLocalTranscribing(currentSessionId, false);
    }
}

// Called from chat-init.js sendMessage when a task is open. Two accepted
// inputs: the draft's request text (draft → queued, planner picks it up) and
// the answer to an awaiting clarifying question (server validates with 409).
// Attached images (v12.6 vision) and audio (voice parity: transcribed into the
// task's context) ride either submit as multipart parts.
function sendTaskInput() {
    if (!window.currentTaskId) return;
    const task = tasksData[window.currentTaskId];
    if (!task) return;
    const input = document.getElementById('message-input');
    const text = input ? input.value.trim() : '';
    const images = taskImageFiles();
    const documents = taskDocumentFiles();
    const audio = taskAudioFiles();
    if (!text && images.length === 0 && documents.length === 0 && audio.length === 0) return;

    const formDataFor = (payloadText, field) => {
        const fd = new FormData();
        fd.append(field || 'request', payloadText);
        fd.append('doc_ids', JSON.stringify(taskDocIds()));
        fd.append('deep_analysis', taskDeepAnalysis() ? 'true' : 'false');
        for (const img of images) fd.append('images', img);
        for (const doc of documents) fd.append('files', doc);
        for (const a of audio) fd.append('audio', a);
        return fd;
    };

    if (task.status === 'draft') {
        input.value = '';
        fetchWithCSRF('/api/tasks/' + encodeURIComponent(window.currentTaskId) + '/request', {
            method: 'POST',
            body: formDataFor(text, 'request'),
        })
            .then(res => res.json().catch(() => ({})))
            .then(data => {
                if (data.error) {
                    if (input) input.value = text;
                    alert(data.error);
                    return;
                }
                if (typeof clearAllAttachments === 'function') clearAllAttachments();
                clearTaskVoiceState();
                if (data.task) tasksData[data.task.id] = Object.assign({}, tasksData[data.task.id], data.task);
                renderTasksList();
                setTaskMode(true, tasksData[window.currentTaskId]);
            })
            .catch(err => {
                if (input) input.value = text;
                alert(t('error') + ': ' + err.message);
            });
        return;
    }

    if (task.status !== 'awaiting_user') {
        // Silent returns misled users: the typed answer sat in the input with
        // no feedback. The task status banner already explains the state
        // (running/queued/...); surface it on the attempt.
        alert(t('task_not_awaiting') + ' ' + taskStatusLine(task));
        return;
    }
    input.value = '';
    fetchWithCSRF('/api/tasks/' + encodeURIComponent(window.currentTaskId) + '/answer', {
        method: 'POST',
        body: formDataFor(text, 'answer'),
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
            if (typeof clearAllAttachments === 'function') clearAllAttachments();
            clearTaskVoiceState();
            scheduleTaskReload(window.currentTaskId);
        })
        .catch(err => {
            if (input) input.value = text;
            alert(t('error') + ': ' + err.message);
        });
}

function retryCurrentTask() {
    if (!window.currentTaskId) return;
    fetchWithCSRF('/api/tasks/' + encodeURIComponent(window.currentTaskId) + '/retry', { method: 'POST' })
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
            if (readRememberedTaskId() === taskId) forgetTaskId();
            if (window.currentTaskId === taskId) {
                // Deleted the open task: open the next one, or the empty state
                // — the Tasks view stays in the main window (user request).
                replaceClosedTask();
            }
            renderTasksList();
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

// ---------------------------------------------------------------------------
// New task creation («+», like a new chat session)
// ---------------------------------------------------------------------------

// «+» creates a draft task right away (no dialog) and opens its view; the
// request text is typed into the ordinary message input (sendTaskInput
// submits it via PATCH /request → the planner picks the task up).
function createNewTask() {
    fetchWithCSRF('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    })
        .then(res => res.json().catch(() => ({})))
        .then(data => {
            if (data.error) {
                alert(data.error);
                return;
            }
            if (data.task && data.task.id) {
                tasksData[data.task.id] = data.task;
                renderTasksList();
                openTaskView(data.task.id);
            } else {
                loadTasksFromServer();
            }
        })
        .catch(err => alert(t('error') + ': ' + err.message));
}

// The list has no tasks: create the first draft task and open it so the Tasks
// view always has a live composer (chat parity — the chat never shows an empty
// session list). Idempotent: an existing task is opened instead of a second one
// being created; only a failed creation request falls back to the empty state
// (offline, tasks disabled).
function ensureFirstTask() {
    const existing = Object.keys(tasksData);
    if (existing.length) {
        openTaskView(existing[0]);
        return;
    }
    // Guard against a double trigger (delete + its SSE event): one draft only.
    if (ensureFirstTask.pending) return;
    ensureFirstTask.pending = true;
    // Claim the window while the request is in flight: closeTaskView cleared
    // currentTaskId and the chat must not repaint over this one.
    tasksViewEmpty = true;
    fetchWithCSRF('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    })
        .then(res => res.json().catch(() => ({})))
        .then(data => {
            ensureFirstTask.pending = false;
            if (data && data.task && data.task.id) {
                tasksData[data.task.id] = data.task;
                renderTasksList();
                openTaskView(data.task.id);
            } else {
                enterTasksEmptyState();
            }
        })
        .catch(() => {
            ensureFirstTask.pending = false;
            enterTasksEmptyState();
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
        if (readRememberedTaskId() === data.task_id) forgetTaskId();
        if (window.currentTaskId === data.task_id) {
            replaceClosedTask();
        }
        renderTasksList();
        scheduleTasksListReload();
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
    scheduleTasksListReload();
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
            createNewTask();
        });
    }
    const stopBtn = document.getElementById('stop-task-button');
    if (stopBtn) {
        stopBtn.addEventListener('click', stopCurrentTask);
    }
    const retryBtn = document.getElementById('retry-task-button');
    if (retryBtn) {
        retryBtn.addEventListener('click', retryCurrentTask);
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