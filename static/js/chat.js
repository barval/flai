// Главный модуль инициализации и обработчиков
import { appState, setCurrentSessionId, cleanupProcessedRequests, clearMessagesCache, setNewMessageIndicator } from './state.js';
import * as api from './api.js';
import * as ui from './ui.js';

let lastUpdateCheck = Date.now() / 1000;
let attachedFile = null;
let isSending = false;

document.addEventListener('DOMContentLoaded', async () => {
    await loadSessions();
    if (appState.currentSessionId) {
        await loadMessages(appState.currentSessionId);
    } else {
        const newSession = await api.createNewSession();
        setCurrentSessionId(newSession.id);
        await loadSessions();
    }
    startGlobalUpdatesPolling();
    initPanelStates();

    // Обработчики событий
    document.getElementById('new-session-button').addEventListener('click', createNewSession);
    document.getElementById('send-button').addEventListener('click', sendMessage);
    document.getElementById('message-input').addEventListener('keypress', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    document.getElementById('attach-file-button').addEventListener('click', () => document.getElementById('file-input').click());
    document.getElementById('file-input').addEventListener('change', handleFileSelect);
    document.getElementById('remove-file-button').addEventListener('click', removeFile);
    document.getElementById('clear-context-button').addEventListener('click', clearHistory);
    document.getElementById('save-chat-button').addEventListener('click', saveChatAsHTML);
    document.getElementById('show-status-button').addEventListener('click', toggleRequestsPanel);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') ui.closeImageModal(); });

    // События от UI
    document.addEventListener('switch-session', (e) => {
        switchSession(e.detail.sessionId);
    });
    document.addEventListener('delete-session', (e) => {
        deleteSession(e.detail.sessionId);
    });

    setInterval(cleanupProcessedRequests, 60000);
    setInterval(() => api.fetchSessions().then(ui.updateSessionsList), 5000);
});

async function loadSessions() {
    const sessions = await api.fetchSessions();
    ui.updateSessionsList(sessions);
    if (!appState.currentSessionId && sessions.length > 0) {
        setCurrentSessionId(sessions[0].id);
    }
}

async function loadMessages(sessionId) {
    if (!sessionId) return;
    const messages = await api.fetchMessages(sessionId);
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';
    let lastUserMessage = null;
    messages.forEach(msg => {
        if (msg.role === 'user') {
            lastUserMessage = msg;
            ui.displayMessage(msg.role, msg.content, msg.file_data, msg.file_type, msg.file_name, msg.timestamp, null, null, null, null, null, null, false); // skipDeduplication = true
        } else if (msg.role === 'assistant') {
            let responseTime = msg.response_time;
            if (lastUserMessage) {
                const userTime = new Date(lastUserMessage.timestamp);
                const assistantTime = new Date(msg.timestamp);
                const diff = (assistantTime - userTime) / 1000;
                if (!responseTime) responseTime = Math.round(diff * 10) / 10;
            }
            ui.displayMessage(msg.role, msg.content, msg.file_data, msg.file_type, msg.file_name, msg.timestamp,
                responseTime, msg.model_name, msg.mm_time, msg.gen_time, msg.mm_model, msg.gen_model, true); // skipDeduplication = true
            lastUserMessage = null;
        }
    });
    ui.updateMessageCount();
    setNewMessageIndicator(sessionId, false);
    clearMessagesCache(sessionId);
}

async function switchSession(sessionId) {
    if (sessionId === appState.currentSessionId) return;
    await api.switchSession(sessionId);
    setCurrentSessionId(sessionId);
    await loadMessages(sessionId);
    ui.updateSessionsList(await api.fetchSessions()); // обновим активный класс
}

async function createNewSession() {
    const newSession = await api.createNewSession();
    setCurrentSessionId(newSession.id);
    await loadSessions();
    document.getElementById('chat-messages').innerHTML = '';
    ui.updateMessageCount();
    setNewMessageIndicator(newSession.id, false);
}

async function deleteSession(sessionId) {
    await api.deleteSession(sessionId);
    // Обновим список сеансов
    const sessions = await api.fetchSessions();
    ui.updateSessionsList(sessions);
    if (sessionId === appState.currentSessionId) {
        if (sessions.length > 0) {
            await switchSession(sessions[0].id);
        } else {
            // Создаём новый сеанс, если не осталось
            await createNewSession();
        }
    }
}

async function sendMessage() {
    if (isSending) return;
    isSending = true;

    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!text && !attachedFile) {
        alert('Введите сообщение или прикрепите файл');
        isSending = false;
        return;
    }

    const sendButton = document.getElementById('send-button');
    sendButton.disabled = true;
    sendButton.innerHTML = '⏳ Отправка...';

    const formData = new FormData();
    formData.append('message', text);
    if (attachedFile) {
        formData.append('file', attachedFile);
    }

    try {
        const data = await api.sendMessage(formData);
        if (data.warning) showServiceWarning(data.warning);
        if (data.status === 'queued') {
            showQueueNotification(data.position, data.estimated_wait);
            startResultPolling(data.request_id);
        } else if (data.response) {
            // Для обратной совместимости (если сервер вернул сразу)
            ui.displayMessage('assistant', data.response, data.generated_image, data.file_type, data.file_name,
                data.assistant_timestamp, data.response_time, data.model_used,
                data.mm_time, data.gen_time, data.mm_model, data.gen_model, false);
        }
    } catch (err) {
        alert('Ошибка: ' + err.message);
    } finally {
        sendButton.disabled = false;
        sendButton.innerHTML = 'Отправить';
        isSending = false;
    }

    // Очищаем поле ввода и файл
    input.value = '';
    attachedFile = null;
    document.getElementById('file-preview-container').style.display = 'none';
    document.getElementById('file-input').value = '';
}

function handleFileSelect(e) {
    if (e.target.files.length > 0) {
        attachedFile = e.target.files[0];
        const preview = document.getElementById('file-preview-container');
        document.getElementById('file-preview-name').textContent = attachedFile.name;
        const sizeSpan = document.getElementById('file-preview-size');
        if (sizeSpan) {
            sizeSpan.textContent = ` (${formatFileSize(attachedFile.size)})`;
        }
        preview.style.display = 'block';
    }
}

function removeFile() {
    attachedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-preview-container').style.display = 'none';
}

async function clearHistory() {
    if (confirm('Очистить всю переписку в этом сеансе?')) {
        await api.clearHistory();
        document.getElementById('chat-messages').innerHTML = '';
        ui.updateMessageCount();
        // Обновим заголовок сеанса в списке
        await loadSessions();
    }
}

async function saveChatAsHTML() {
    const footerText = await api.fetchFooterText();
    const activeSession = document.querySelector('.session-item.active');
    if (!activeSession) {
        alert('Нет активного сеанса для сохранения');
        return;
    }
    const title = activeSession.querySelector('.session-title')?.textContent || 'Чат';
    const now = new Date();
    const timestamp = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;

    // Получаем все сообщения
    const messages = [];
    document.querySelectorAll('.user-message, .assistant-message').forEach(msgEl => {
        const role = msgEl.classList.contains('user-message') ? 'user' : 'assistant';
        const timeEl = msgEl.querySelector('small');
        const contentEl = msgEl.querySelector('.message-content');
        const imageEl = msgEl.querySelector('.attached-image');
        const audioEl = msgEl.querySelector('audio');
        const fileEl = msgEl.querySelector('.attached-file');
        messages.push({
            role,
            timeHtml: timeEl ? timeEl.innerHTML : '',
            contentHtml: contentEl ? contentEl.innerHTML : '',
            imageHtml: imageEl ? imageEl.outerHTML : '',
            audioHtml: audioEl ? audioEl.outerHTML : '',
            fileHtml: fileEl ? fileEl.outerHTML : ''
        });
    });

    // Загружаем стили с сервера (для экспорта)
    const cssResponse = await fetch('/static/style.css');
    const cssText = await cssResponse.text();

    const html = `<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>${escapeHtml(title)}</title><style>${cssText}</style></head>
<body>
    <header><h1>ИИ Локальный</h1></header>
    <main><div class="chat-wrapper">${messages.map(m => `
        <div class="${m.role}-message">
            <small class="message-time">${m.timeHtml}</small>
            <div class="message-content">${m.contentHtml}</div>
            ${m.imageHtml}${m.audioHtml}${m.fileHtml}
        </div>`).join('')}
    </div></main>
    <footer><div class="footer-content">${footerText}</div></footer>
</body>
</html>`;

    const blob = new Blob([html], {type: 'text/html'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat_${timestamp}.html`;
    a.click();
    URL.revokeObjectURL(url);
}

function showQueueNotification(position, waitSeconds) {
    const waitText = waitSeconds > 60 ? `${Math.floor(waitSeconds/60)} мин ${waitSeconds%60} сек` : `${waitSeconds} сек`;
    const notification = document.createElement('div');
    notification.className = 'queue-notification';
    notification.innerHTML = `
        <div class="notification-content">
            <span>⏳ Запрос в очереди (позиция ${position})</span>
            <span class="wait-time">~${waitText}</span>
            <button class="show-requests-button" onclick="document.getElementById('show-status-button').click(); this.parentElement.parentElement.remove()">📊 Показать статус</button>
        </div>
    `;
    document.querySelector('.chat-header').appendChild(notification);
    setTimeout(() => notification.remove(), 8000);
}

function startResultPolling(requestId) {
    if (appState.processedRequests.has(requestId)) return;
    appState.processedRequests.add(requestId);
    appState.requestProcessingTimes.set(requestId, Date.now());

    let pollCount = 0;
    const maxPolls = 120;
    const interval = setInterval(async () => {
        pollCount++;
        try {
            const data = await api.checkResult(requestId);
            if (data.status === 'completed' && data.result) {
                const result = data.result;
                if (result.session_id === appState.currentSessionId) {
                    ui.displayMessage('assistant', result.response, result.generated_image, result.file_type, result.file_name,
                        result.assistant_timestamp, result.response_time, result.model_used,
                        result.mm_time, result.gen_time, result.mm_model, result.gen_model, false);
                } else {
                    setNewMessageIndicator(result.session_id, true, result.is_error);
                }
                api.fetchQueueStatus().then(updateRequestsStatus);
                clearInterval(interval);
                appState.processedRequests.delete(requestId);
                appState.requestProcessingTimes.delete(requestId);
                return;
            } else if (data.status === 'error') {
                if (data.result?.session_id === appState.currentSessionId) {
                    ui.displayMessage('assistant', `⚠️ Ошибка: ${data.error}`, null, null, null, new Date().toISOString(), null, 'system', null, null, null, null, false);
                } else if (data.result?.session_id) {
                    setNewMessageIndicator(data.result.session_id, true, true);
                }
                clearInterval(interval);
                appState.processedRequests.delete(requestId);
                appState.requestProcessingTimes.delete(requestId);
                return;
            }
            if (pollCount >= maxPolls) {
                ui.displayMessage('assistant', '⚠️ Превышено время ожидания ответа.', null, null, null, new Date().toISOString(), null, 'system', null, null, null, null, false);
                clearInterval(interval);
                appState.processedRequests.delete(requestId);
                appState.requestProcessingTimes.delete(requestId);
            }
        } catch (err) {
            console.error('Polling error:', err);
            // При ошибке сети не останавливаем интервал, продолжаем попытки
        }
    }, 3000);
}

function showServiceWarning(message, type = 'warning') {
    const warningsDiv = document.getElementById('service-warnings');
    if (!warningsDiv) return;
    const warning = document.createElement('div');
    warning.className = `service-warning ${type}`;
    warning.innerHTML = `<span class="warning-icon">⚠️</span><span class="warning-text">${message}</span><button class="close-warning" onclick="this.parentElement.remove()">✕</button>`;
    warningsDiv.appendChild(warning);
    setTimeout(() => warning.remove(), 10000);
}

function initPanelStates() {
    const requestsPanel = document.getElementById('requests-status-panel');
    const requestsOpen = localStorage.getItem('requestsPanelOpen') === 'true';
    requestsPanel.style.display = requestsOpen ? 'block' : 'none';
    if (requestsOpen) startStatusRefresh();
}

function toggleRequestsPanel() {
    const panel = document.getElementById('requests-status-panel');
    const isHidden = panel.style.display === 'none';
    panel.style.display = isHidden ? 'block' : 'none';
    localStorage.setItem('requestsPanelOpen', isHidden);
    if (isHidden) {
        startStatusRefresh();
    } else {
        stopStatusRefresh();
    }
}

function startStatusRefresh() {
    if (appState.intervals.status) clearInterval(appState.intervals.status);
    appState.intervals.status = setInterval(() => {
        api.fetchQueueStatus().then(updateRequestsStatus);
    }, 3000);
}

function stopStatusRefresh() {
    if (appState.intervals.status) {
        clearInterval(appState.intervals.status);
        appState.intervals.status = null;
    }
}

function updateRequestsStatus(status) {
    // Обновление панели "Мои запросы"
    if (status.processing) {
        document.getElementById('processing-request').style.display = 'block';
        document.getElementById('processing-text').innerHTML = `${status.processing.type_icon} ${status.processing.session_title}: обрабатывается...`;
    } else {
        document.getElementById('processing-request').style.display = 'none';
    }

    const queuedList = document.getElementById('queued-list');
    if (status.queued?.length) {
        document.getElementById('queued-requests').style.display = 'block';
        queuedList.innerHTML = status.queued.map(req => `
            <div class="queue-item" data-request-id="${req.id}">
                <span class="queue-position">#${req.position_info.position}</span>
                <span class="request-icon">${req.type_icon}</span>
                <span class="request-title">${req.session_title.substring(0,20)}...</span>
                <span class="wait-time">⏱️ ${req.position_info.estimated_seconds}с</span>
                <span class="cancel-request" onclick="cancelRequest('${req.id}')">✕</span>
            </div>
        `).join('');
    } else {
        document.getElementById('queued-requests').style.display = 'none';
    }

    // Обновление системной нагрузки
    const system = status.system;
    if (system) {
        const loadPercent = Math.min(100, (system.total_queued / 10) * 100);
        const loadClass = system.total_queued > 10 ? 'high' : '';
        let statusText = system.total_queued > 10 ? 'Высокая' : system.total_queued > 5 ? 'Средняя' : 'Низкая';
        let statusColor = system.total_queued > 10 ? '#dc3545' : system.total_queued > 5 ? '#ffc107' : '#28a745';
        document.getElementById('system-load').innerHTML = `
            <div class="system-load-indicator">
                <div class="load-header"><span>📊 Нагрузка системы</span><span class="load-status" style="color:${statusColor};">${statusText}</span></div>
                <div class="load-bar-container">
                    <div class="load-bar"><div class="load-fill ${loadClass}" style="width:${loadPercent}%"></div></div>
                    <div class="load-value"><strong>${system.total_queued}</strong> в очереди</div>
                </div>
            </div>
        `;
    }
}

function startGlobalUpdatesPolling() {
    if (appState.intervals.globalUpdates) clearInterval(appState.intervals.globalUpdates);
    appState.intervals.globalUpdates = setInterval(async () => {
        try {
            const updates = await api.checkUpdates(lastUpdateCheck);
            if (updates.has_updates) {
                await loadSessions();
                if (updates.current_session_updated) {
                    clearMessagesCache(appState.currentSessionId);
                    await loadMessages(appState.currentSessionId);
                }
                if (updates.new_messages) {
                    updates.new_messages.forEach(sid => {
                        if (sid !== appState.currentSessionId) setNewMessageIndicator(sid, true);
                    });
                }
                lastUpdateCheck = Date.now() / 1000;
            }
        } catch (err) {
            console.error('Updates polling error:', err);
        }
    }, 2000);
}

async function cancelRequest(requestId) {
    if (!confirm('Отменить запрос?')) return;
    await api.cancelRequest(requestId);
    api.fetchQueueStatus().then(updateRequestsStatus);
}

// Вспомогательная функция для pad (дублируется, но можно импортировать)
function pad(n) { return n.toString().padStart(2, '0'); }
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
function formatFileSize(bytes) {
    if (!bytes) return '';
    const k = 1024;
    const sizes = ['Б', 'КБ', 'МБ', 'ГБ'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}