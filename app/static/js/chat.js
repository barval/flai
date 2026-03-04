// static/js/chat.js
// Глобальные переменные (будут инициализированы в шаблоне)
let currentSessionId = window.initialSessionId;
let isSending = false;
let attachedFile = null;
let pendingRequests = {};
let defaultModelName = 'qwen3-vl:8b-instruct';
let sessionsData = {};
let syncInterval = null;
let newMessageIndicators = {};
let sessionQueueInfo = {};          // агрегированная информация о статусах задач
let stableSessionStatus = {};       // стабилизированные статусы (чтобы избежать мерцания)
let lastCompletionTime = {};
let sessionsUpdateTimeout = null;

// Переменные для записи голоса
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isVoiceRecorded = false;

// -------------------------------
// Вспомогательные функции
// -------------------------------
function pad(n) {
    return n.toString().padStart(2, '0');
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Б';
    if (!bytes) return '';
    const k = 1024;
    const sizes = ['Б', 'КБ', 'МБ', 'ГБ'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function decodeHtmlEntities(text) {
    if (!text) return '';
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatFullDateTime(ts) {
    if (!ts) return '';
    try {
        const date = new Date(ts);
        if (isNaN(date.getTime())) {
            return ts.replace('T', ' ').slice(0, 19);
        }
        const year = date.getFullYear();
        const month = (date.getMonth() + 1).toString().padStart(2, '0');
        const day = date.getDate().toString().padStart(2, '0');
        const hours = date.getHours().toString().padStart(2, '0');
        const minutes = date.getMinutes().toString().padStart(2, '0');
        const seconds = date.getSeconds().toString().padStart(2, '0');
        return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
    } catch (e) {
        return ts.replace('T', ' ').slice(0, 19);
    }
}

// -------------------------------
// Модальное окно изображений
// -------------------------------
function openImageModal(imgSrc, imgAlt) {
    const modal = document.getElementById('image-modal');
    const modalImg = document.getElementById('modal-image');
    const captionText = document.getElementById('modal-caption');
    modal.style.display = "block";
    modalImg.src = imgSrc;
    captionText.innerHTML = imgAlt;
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    modal.style.display = "none";
}

// -------------------------------
// Обновление счётчика сообщений
// -------------------------------
function updateMessageCount() {
    const count = document.querySelectorAll('.user-message, .assistant-message, .bot-message').length;
    document.getElementById('context-info').textContent = `Сообщений: ${count}`;
}

// -------------------------------
// Управление индикаторами новых сообщений
// -------------------------------
function setNewMessageIndicator(sessionId, show) {
    if (show) {
        newMessageIndicators[sessionId] = true;
    } else {
        delete newMessageIndicators[sessionId];
    }
    updateSessionsListFromData();
}

// -------------------------------
// Получение статусов очереди (агрегированных)
// -------------------------------
function fetchQueueStatus() {
    fetch('/api/queue/status')
        .then(res => res.json())
        .then(data => {
            const now = Date.now();
            const agg = {}; // агрегированные данные по сеансам

            // Задача в обработке (processing)
            if (data.processing) {
                const proc = data.processing;
                if (!agg[proc.session_id]) agg[proc.session_id] = { processing: false, queued: 0 };
                agg[proc.session_id].processing = true;
            }

            // Задачи в очереди (queued)
            data.queued.forEach(item => {
                if (!agg[item.session_id]) agg[item.session_id] = { processing: false, queued: 0 };
                agg[item.session_id].queued += 1;
            });

            // Стабилизация: применяем новый статус, только если он держится не менее двух циклов (6 секунд)
            const newStable = {};
            Object.keys(agg).forEach(sid => {
                const current = agg[sid];
                const prev = stableSessionStatus[sid];
                if (!prev || prev.processing !== current.processing || prev.queued !== current.queued) {
                    // Статус изменился — запоминаем время изменения
                    if (!prev || prev.pendingChange) {
                        // Если уже было запланировано изменение, проверяем, прошло ли 6 секунд
                        if (prev && now - prev.changeTime > 6000) {
                            // Применяем изменение
                            newStable[sid] = {
                                processing: current.processing,
                                queued: current.queued,
                                changeTime: now,
                                pendingChange: false
                            };
                        } else {
                            // Ещё не прошло 6 секунд — оставляем старый статус, но помечаем ожидание
                            newStable[sid] = {
                                ...prev,
                                pendingChange: true,
                                changeTime: prev ? prev.changeTime : now
                            };
                        }
                    } else {
                        // Первое изменение
                        newStable[sid] = {
                            ...current,
                            changeTime: now,
                            pendingChange: true
                        };
                    }
                } else {
                    // Статус не изменился — просто копируем
                    newStable[sid] = { ...current, changeTime: now, pendingChange: false };
                }
            });

            // Удаляем устаревшие сеансы (которых нет в agg более 10 секунд)
            Object.keys(stableSessionStatus).forEach(sid => {
                if (!agg[sid] && (now - stableSessionStatus[sid].changeTime) > 10000) {
                    delete stableSessionStatus[sid];
                }
            });

            stableSessionStatus = newStable;

            // Преобразуем стабилизированные статусы в итоговый объект sessionQueueInfo
            // (убираем служебные поля changeTime, pendingChange)
            sessionQueueInfo = {};
            Object.keys(stableSessionStatus).forEach(sid => {
                sessionQueueInfo[sid] = {
                    processing: stableSessionStatus[sid].processing,
                    queued: stableSessionStatus[sid].queued
                };
            });

            updateSessionsListFromData();
        })
        .catch(err => console.error('Ошибка получения статусов очереди:', err));
}

// -------------------------------
// Загрузка списка сеансов
// -------------------------------
function loadSessionsFromServer() {
    return fetch('/api/sessions')
        .then(res => res.json())
        .then(sessions => {
            let updated = false;
            sessions.forEach(s => {
                if (!sessionsData[s.id]) {
                    sessionsData[s.id] = {
                        title: s.title,
                        updated_at: s.updated_at
                    };
                    updated = true;
                } else {
                    if (sessionsData[s.id].title !== s.title) {
                        sessionsData[s.id].title = s.title;
                        sessionsData[s.id].updated_at = s.updated_at;
                        updated = true;
                    } else if (sessionsData[s.id].updated_at !== s.updated_at) {
                        sessionsData[s.id].updated_at = s.updated_at;
                        updated = true;
                    }
                }
                const prevUnread = newMessageIndicators[s.id] ? true : false;
                const newUnread = s.has_unread ? true : false;
                if (prevUnread !== newUnread) {
                    updated = true;
                }
                if (s.has_unread) {
                    newMessageIndicators[s.id] = true;
                } else {
                    delete newMessageIndicators[s.id];
                }
            });
            Object.keys(sessionsData).forEach(id => {
                if (!sessions.find(s => s.id === id)) {
                    delete sessionsData[id];
                    delete newMessageIndicators[id];
                    delete lastCompletionTime[id];
                    updated = true;
                }
            });
            if (updated) {
                updateSessionsList(sessions);
            }
            return sessions;
        })
        .catch(err => console.error('Ошибка загрузки сеансов:', err));
}

// -------------------------------
// Обновление списка сеансов в DOM
// -------------------------------
function updateSessionsListFromData() {
    if (sessionsUpdateTimeout) {
        clearTimeout(sessionsUpdateTimeout);
    }
    sessionsUpdateTimeout = setTimeout(() => {
        const sessions = Object.keys(sessionsData).map(id => ({
            id: id,
            title: sessionsData[id].title,
            updated_at: sessionsData[id].updated_at
        }));
        updateSessionsList(sessions);
        sessionsUpdateTimeout = null;
    }, 100);
}

function updateSessionsList(sessions) {
    const sessionsList = document.getElementById('sessions-list');
    const currentActiveId = currentSessionId;

    sessions.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));

    let html = '';
    sessions.forEach(s => {
        const isActive = s.id === currentActiveId ? 'active' : '';
        const dateStr = s.updated_at ? s.updated_at.replace('T', ' ').substring(0, 19) : '';
        
        // Определяем значок статуса по приоритету
        let statusIcons = '';
        const info = sessionQueueInfo[s.id];
        if (info) {
            if (info.processing) {
                statusIcons = '<span class="session-status-icon processing blink" title="Обрабатывается">⚡</span>';
            } else if (info.queued > 0) {
                const count = info.queued > 1 ? ` ${info.queued}` : '';
                statusIcons = '<span class="session-status-icon queued" title="В очереди">⏳' + count + '</span>';
            }
        }
        if (!statusIcons && newMessageIndicators[s.id] && s.id !== currentActiveId) {
            statusIcons = '<span class="session-status-icon unread blink" title="Новый ответ">✉️</span>';
        }

        html += `
            <div class="session-item ${isActive}" data-session-id="${s.id}" data-session-title="${escapeHtml(s.title)}">
                <div class="session-content">
                    <div class="session-info">
                        <div class="session-title">
                            ${statusIcons}
                            ${escapeHtml(s.title)}
                        </div>
                        <div class="session-date">${dateStr}</div>
                    </div>
                    <button class="delete-session-button" title="Удалить сеанс">🗑️</button>
                </div>
            </div>
        `;
    });

    sessionsList.innerHTML = html;
    document.getElementById('sessions-count').textContent = sessions.length;
    attachSessionEventHandlers();
}

function attachSessionEventHandlers() {
    document.querySelectorAll('.session-item').forEach(el => {
        el.addEventListener('click', function(e) {
            if (e.target.closest('.delete-session-button')) return;
            const sessionId = this.dataset.sessionId;
            if (sessionId === currentSessionId) return;
            setNewMessageIndicator(sessionId, false);
            document.querySelectorAll('.session-item').forEach(i => i.classList.remove('active'));
            this.classList.add('active');
            switchSession(sessionId);
        });
    });
    document.querySelectorAll('.delete-session-button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const sessionItem = this.closest('.session-item');
            const sessionId = sessionItem.dataset.sessionId;
            const sessionTitle = sessionItem.dataset.sessionTitle;
            const sessionDate = sessionItem.querySelector('.session-date').textContent;
            if (confirm(`Удалить сеанс "${sessionTitle}" от ${sessionDate}?`)) {
                deleteSession(sessionId);
            }
        });
    });
}

// -------------------------------
// Периодическая синхронизация
// -------------------------------
function startSyncInterval() {
    if (syncInterval) clearInterval(syncInterval);
    syncInterval = setInterval(() => {
        loadSessionsFromServer();
        fetchQueueStatus();
        window.updateStatusCounter();
    }, 5000);
}

window.updateStatusCounter = function() {
    fetch('/api/queue/counts')
        .then(response => response.json())
        .then(data => {
            const counter = document.getElementById('status-counter');
            if (counter) {
                counter.textContent = `📊 ${data.user_queued}/${data.total_queued}`;
            }
        })
        .catch(err => console.error('Ошибка обновления счётчика:', err));
};

function updateLastVisit(sessionId) {
    fetch(`/api/sessions/${sessionId}/visit`, { method: 'POST' })
        .catch(err => console.error('Ошибка обновления last_visit:', err));
}

// -------------------------------
// Инициализация после загрузки DOM
// -------------------------------
document.addEventListener('DOMContentLoaded', function() {
    loadSessionsFromServer().then(() => {
        loadMessages(currentSessionId);
        startSyncInterval();
    });

    document.getElementById('new-session-button').addEventListener('click', createNewSession);
    document.getElementById('send-button').addEventListener('click', sendMessage);
    document.getElementById('message-input').addEventListener('keypress', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    document.getElementById('attach-file-button').addEventListener('click', function() {
        document.getElementById('file-input').click();
    });

    document.getElementById('file-input').addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            attachedFile = e.target.files[0];
            const preview = document.getElementById('file-preview-container');
            document.getElementById('file-preview-name').textContent = attachedFile.name;
            const fileSize = formatFileSize(attachedFile.size);
            const sizeSpan = document.getElementById('file-preview-size');
            if (sizeSpan) sizeSpan.textContent = ` (${fileSize})`;
            preview.style.display = 'block';
        }
    });

    document.getElementById('remove-file-button').addEventListener('click', function() {
        attachedFile = null;
        document.getElementById('file-input').value = '';
        document.getElementById('file-preview-container').style.display = 'none';
    });

    document.getElementById('save-chat-button').addEventListener('click', saveChatAsHTML);

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') closeImageModal();
    });

    window.updateStatusCounter();
    fetchQueueStatus();

    document.getElementById('voice-record-button').addEventListener('click', toggleVoiceRecording);
});

// -------------------------------
// Загрузка сообщений сеанса
// -------------------------------
function loadMessages(sessionId) {
    return fetch(`/api/sessions/${sessionId}/messages`)
        .then(res => res.json())
        .then(messages => {
            const container = document.getElementById('chat-messages');
            container.innerHTML = '';

            fetch(`/api/sessions/${sessionId}/model-info`)
                .then(res => res.json())
                .then(data => {
                    defaultModelName = data.model_name || 'qwen3-vl:8b-instruct-q4_K_M';
                })
                .catch(err => console.error('Error loading model info:', err));

            let lastUserMessage = null;

            messages.forEach((msg) => {
                if (msg.role === 'user') {
                    lastUserMessage = msg;
                    displayMessage(
                        msg.role,
                        msg.content,
                        msg.file_data,
                        msg.file_type,
                        msg.file_name,
                        msg.timestamp,
                        null, null, null, null, null, null
                    );
                } else if (msg.role === 'assistant') {
                    let responseTime = null;
                    if (lastUserMessage) {
                        const userTime = new Date(lastUserMessage.timestamp);
                        const assistantTime = new Date(msg.timestamp);
                        const diffSeconds = (assistantTime - userTime) / 1000;
                        responseTime = Math.round(diffSeconds * 10) / 10;
                    }
                    if (msg.response_time) {
                        if (typeof msg.response_time === 'object') {
                            responseTime = msg.response_time;
                        } else if (!isNaN(parseFloat(msg.response_time))) {
                            responseTime = parseFloat(msg.response_time);
                        }
                    }
                    let mmTime = msg.mm_time;
                    let genTime = msg.gen_time;
                    let mmModel = msg.mm_model;
                    let genModel = msg.gen_model;
                    if (mmTime && genTime) {
                        responseTime = {
                            mm_time: parseFloat(mmTime),
                            gen_time: parseFloat(genTime),
                            mm_model: mmModel || 'неизвестно',
                            gen_model: genModel || 'неизвестно'
                        };
                    }
                    displayMessage(
                        msg.role,
                        msg.content,
                        msg.file_data,
                        msg.file_type,
                        msg.file_name,
                        msg.timestamp,
                        responseTime,
                        msg.model_name || defaultModelName,
                        mmTime,
                        genTime,
                        mmModel,
                        genModel
                    );
                    lastUserMessage = null;
                }
            });

            updateMessageCount();
            container.scrollTop = container.scrollHeight;
            setNewMessageIndicator(sessionId, false);
            updateLastVisit(sessionId);
        });
}

// -------------------------------
// Отображение одного сообщения
// -------------------------------
function displayMessage(role, content, fileData, fileType, fileName, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel) {
    const container = document.getElementById('chat-messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = (role === 'user') ? 'user-message' : 'assistant-message bot-message';
    if (!timestamp) timestamp = new Date().toISOString();
    msgDiv.setAttribute('data-timestamp', timestamp);

    if (role === 'assistant') {
        if (modelName) msgDiv.dataset.modelName = modelName;
        if (responseTime && typeof responseTime === 'object') {
            if (responseTime.mm_time) msgDiv.dataset.mmTime = responseTime.mm_time;
            if (responseTime.gen_time) msgDiv.dataset.genTime = responseTime.gen_time;
            if (responseTime.mm_model) msgDiv.dataset.mmModel = responseTime.mm_model;
            if (responseTime.gen_model) msgDiv.dataset.genModel = responseTime.gen_model;
        } else if (mmTime && genTime) {
            msgDiv.dataset.mmTime = mmTime;
            msgDiv.dataset.genTime = genTime;
            msgDiv.dataset.mmModel = mmModel || 'неизвестно';
            msgDiv.dataset.genModel = genModel || 'неизвестно';
        }
    }

    let timeDisplay = formatFullDateTime(timestamp);

    if (role === 'user' && fileName && fileData) {
        const base64Length = fileData.length;
        const fileSizeBytes = Math.round((base64Length * 3) / 4);
        const fileSize = formatFileSize(fileSizeBytes);
        timeDisplay += ` <span class="file-info">[📎 ${fileName}, ${fileSize}]</span>`;
        if (fileType && fileType.startsWith('image/')) {
            timeDisplay += ` <a href="data:${fileType};base64,${fileData}" download="${fileName || 'image.jpg'}" class="download-link-inline" title="Скачать изображение" onclick="event.stopPropagation()">⬇️</a>`;
        }
        if (fileType && fileType.startsWith('audio/')) {
            timeDisplay += ` <a href="data:${fileType};base64,${fileData}" download="${fileName || 'audio.webm'}" class="download-link-inline" title="Скачать аудио" onclick="event.stopPropagation()">⬇️</a>`;
        }
    }
    if (role === 'assistant' && fileName && fileData) {
        const base64Length = fileData.length;
        const fileSizeBytes = Math.round((base64Length * 3) / 4);
        const fileSize = formatFileSize(fileSizeBytes);
        timeDisplay += ` <span class="file-info">[📎 ${fileName}, ${fileSize}]</span>`;
        if (fileType && fileType.startsWith('image/')) {
            timeDisplay += ` <a href="data:${fileType};base64,${fileData}" download="${fileName || 'generated_image.jpg'}" class="download-link-inline" title="Скачать изображение" onclick="event.stopPropagation()">⬇️</a>`;
        }
        if (fileType && fileType.startsWith('audio/')) {
            timeDisplay += ` <a href="data:${fileType};base64,${fileData}" download="${fileName || 'audio.webm'}" class="download-link-inline" title="Скачать аудио" onclick="event.stopPropagation()">⬇️</a>`;
        }
    }

    let headerHTML = `<span class="message-header">📅 ${timeDisplay}`;
    if (role === 'assistant') {
        if (modelName) {
            const shortModel = modelName.split('/').pop() || modelName;
            headerHTML += ` <span class="text-muted">| ${escapeHtml(shortModel)}</span>`;
        }
        let duration = null;
        if (responseTime) {
            if (typeof responseTime === 'object') {
                if (responseTime.mm_time && responseTime.gen_time) {
                    duration = (parseFloat(responseTime.mm_time) + parseFloat(responseTime.gen_time)).toFixed(1);
                } else if (responseTime.mm_time) {
                    duration = parseFloat(responseTime.mm_time).toFixed(1);
                } else if (responseTime.gen_time) {
                    duration = parseFloat(responseTime.gen_time).toFixed(1);
                }
            } else if (typeof responseTime === 'number' || !isNaN(parseFloat(responseTime))) {
                duration = parseFloat(responseTime).toFixed(1);
            }
        }
        if (duration) headerHTML += ` <span class="text-muted">⏱️ ${duration}с</span>`;
    }
    headerHTML += '</span>';

    let contentHTML = `<div class="message-content">`;
    if (typeof content === 'string') {
        if (content.startsWith('[')) {
            try {
                const parts = JSON.parse(content);
                let textContent = '';
                parts.forEach(part => {
                    if (part.type === 'text') textContent += part.text + '\n';
                });
                if (textContent) {
                    const escapedText = escapeHtml(textContent.trim());
                    contentHTML += marked.parse(escapedText);
                }
            } catch (e) {
                const decodedText = (role === 'assistant') ? decodeHtmlEntities(content) : escapeHtml(content);
                contentHTML += marked.parse(decodedText);
            }
        } else {
            const decodedText = (role === 'assistant') ? decodeHtmlEntities(content) : escapeHtml(content);
            contentHTML += marked.parse(decodedText);
        }
    }
    contentHTML += '</div>';
    msgDiv.innerHTML = headerHTML + contentHTML;

    if (fileData) {
        let fileHTML = '';
        if (fileType && fileType.startsWith('image/')) {
            fileHTML = `
                <div class="image-container">
                    <img src="data:${fileType};base64,${fileData}" class="attached-image" alt="${fileName || 'attached image'}" title="Нажмите для увеличения" onclick="openImageModal(this.src, '${fileName || 'Изображение'}')">
                </div>
            `;
        } else if (fileType && fileType.startsWith('audio/')) {
            fileHTML = `<audio controls src="data:${fileType};base64,${fileData}"></audio>`;
        } else {
            fileHTML = `<div class="attached-file"><span class="file-icon">📄</span><a href="data:${fileType};base64,${fileData}" download="${fileName}">${fileName}</a></div>`;
        }
        msgDiv.innerHTML += fileHTML;
    }

    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
    updateMessageCount();

    setTimeout(() => {
        addCopyButtonsToMessage(msgDiv);
    }, 50);
}

// -------------------------------
// Переключение сеанса
// -------------------------------
function switchSession(sessionId) {
    fetch(`/api/sessions/${sessionId}/switch`, { method: 'POST' })
        .then(res => res.json())
        .then(() => {
            currentSessionId = sessionId;
            loadMessages(sessionId);
            document.querySelectorAll('.session-item').forEach(el => {
                if (el.dataset.sessionId === sessionId) {
                    el.classList.add('active');
                } else {
                    el.classList.remove('active');
                }
            });
            updateSessionsListFromData();
        });
}

// -------------------------------
// Создание нового сеанса
// -------------------------------
function createNewSession() {
    fetch('/api/sessions/new', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            sessionsData[data.id] = {
                title: data.title,
                updated_at: new Date().toISOString()
            };
            document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
            currentSessionId = data.id;
            loadSessionsFromServer().then(() => {
                document.getElementById('chat-messages').innerHTML = '';
                updateMessageCount();
                defaultModelName = 'qwen3-vl:8b-instruct';
                setNewMessageIndicator(data.id, false);
            });
        });
}

// -------------------------------
// Обновление заголовка сеанса
// -------------------------------
function updateSessionTitle(sessionId, newTitle) {
    if (sessionsData[sessionId]) {
        sessionsData[sessionId].title = newTitle;
        sessionsData[sessionId].updated_at = new Date().toISOString();
    }
    const sessionItem = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
    if (!sessionItem) return;
    const titleElement = sessionItem.querySelector('.session-title');
    if (titleElement) titleElement.innerHTML = escapeHtml(newTitle);
    const now = new Date();
    const formattedDate = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    const dateElement = sessionItem.querySelector('.session-date');
    if (dateElement) dateElement.textContent = formattedDate;
    const sessionsList = document.getElementById('sessions-list');
    if (sessionsList.firstChild !== sessionItem) {
        sessionsList.insertBefore(sessionItem, sessionsList.firstChild);
    }
}

// -------------------------------
// Запись голоса
// -------------------------------
async function toggleVoiceRecording() {
    if (isRecording) {
        await stopRecording();
    } else {
        await startRecording();
    }
}

async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert('Ваш браузер не поддерживает запись audio');
        return;
    }
    if (!window.isSecureContext) {
        alert('Для записи голоса требуется безопасное соединение (HTTPS).\nПопробуйте открыть сайт по HTTPS или localhost.');
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        mediaRecorder.ondataavailable = event => {
            if (event.data.size > 0) audioChunks.push(event.data);
        };
        mediaRecorder.onstop = () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            sendVoiceMessage(audioBlob);
            stream.getTracks().forEach(track => track.stop());
        };
        mediaRecorder.start();
        isRecording = true;
        document.getElementById('voice-record-button').classList.add('recording');
        document.getElementById('recording-indicator').style.display = 'inline';
    } catch (err) {
        console.error('Ошибка доступа к микрофону:', err);
        alert('Не удалось получить доступ к микрофону. Убедитесь, что сайт открыт по HTTPS или localhost, и разрешите использование микрофона.');
    }
}

async function stopRecording() {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        isRecording = false;
        document.getElementById('voice-record-button').classList.remove('recording');
        document.getElementById('recording-indicator').style.display = 'none';
    }
}

async function sendVoiceMessage(blob) {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    const filename = `voice_${year}${month}${day}_${hours}${minutes}${seconds}.webm`;
    const file = new File([blob], filename, { type: 'audio/webm' });
    attachedFile = file;
    isVoiceRecorded = true;
    const preview = document.getElementById('file-preview-container');
    document.getElementById('file-preview-name').textContent = file.name;
    const fileSize = formatFileSize(file.size);
    document.getElementById('file-preview-size').textContent = ` (${fileSize})`;
    preview.style.display = 'block';
    sendMessage();
}

// -------------------------------
// Отправка сообщения
// -------------------------------
async function sendMessage() {
    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!text && !attachedFile) {
        alert('Введите сообщение или прикрепите файл');
        return;
    }
    if (isSending) return;
    isSending = true;

    const sendButton = document.getElementById('send-button');
    sendButton.disabled = true;
    sendButton.innerHTML = '⏳ Отправка...';

    const messageCount = document.querySelectorAll('.user-message').length;
    if (messageCount === 0) {
        let newTitle = text ? text.slice(0, 40) + (text.length > 40 ? '...' : '') : '';
        if (!newTitle && attachedFile) {
            newTitle = attachedFile.name.slice(0, 40) + (attachedFile.name.length > 40 ? '...' : '');
        }
        if (newTitle) {
            updateSessionTitle(currentSessionId, newTitle);
            fetch(`/api/sessions/${currentSessionId}/update-title`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({title: newTitle})
            }).catch(err => console.error('Ошибка обновления заголовка:', err));
        }
    }

    delete lastCompletionTime[currentSessionId];

    const now = new Date();
    const timestamp = now.toISOString();

    const userContent = [];
    if (text) userContent.push({"type": "text", "text": text});

    let fileData = null, fileType = null, fileName = null;
    const tempAttachedFile = attachedFile;
    const tempText = text;

    const displayUserMessage = (fileData, fileType, fileName) => {
        if (fileData) {
            let type = "file";
            if (fileType && fileType.startsWith('image/')) type = "image";
            else if (fileType && fileType.startsWith('audio/')) type = "audio";
            userContent.push({ "type": type, "file_data": fileData, "file_type": fileType, "file_name": fileName });
        }
        displayMessage('user', JSON.stringify(userContent), fileData, fileType, fileName, timestamp);
        input.value = '';
        attachedFile = null;
        document.getElementById('file-preview-container').style.display = 'none';
        document.getElementById('file-input').value = '';
    };

    const sendToServer = async () => {
        try {
            let response;
            if (tempAttachedFile) {
                const formData = new FormData();
                formData.append('message', tempText);
                formData.append('file', tempAttachedFile);
                if (isVoiceRecorded) {
                    formData.append('voice_record', 'true');
                    isVoiceRecorded = false;
                }
                response = await fetch('/send_message', { method: 'POST', body: formData });
            } else {
                response = await fetch('/send_message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: tempText })
                });
            }
            const data = await response.json();
            console.log('Ответ от сервера:', data);

            if (data.transcribed_text) {
                if (data.session_id && data.session_id === currentSessionId) {
                    displayMessage('assistant', `🎤 Распознано: ${data.transcribed_text}`, null, null, null,
                        new Date().toISOString(), data.response_time, 'whisper');
                } else if (data.session_id) {
                    setNewMessageIndicator(data.session_id, true);
                } else {
                    displayMessage('assistant', `🎤 Распознано: ${data.transcribed_text}`, null, null, null,
                        new Date().toISOString(), data.response_time, 'whisper');
                }
                sendButton.disabled = false;
                sendButton.innerHTML = 'Отправить';
                isSending = false;
                if (!data.request_id) return;
            }

            if (data.status === 'queued') {
                // Статус будет обновлён через fetchQueueStatus, поэтому не меняем локально
                pendingRequests[data.request_id] = { sessionId: currentSessionId, processed: false };
                window.updateStatusCounter();
                startResultPolling(data.request_id);
            } else if (data.response) {
                displayMessage('assistant', data.response, data.generated_image, data.file_type, data.file_name,
                    data.assistant_timestamp, data.response_time, data.model_used);
            }
        } catch (err) {
            alert('Ошибка: ' + err.message);
            console.error('Send message error:', err);
            const lastMessage = document.querySelector('.user-message:last-child');
            if (lastMessage) lastMessage.style.borderLeft = '3px solid #e74c3c';
        } finally {
            sendButton.disabled = false;
            sendButton.innerHTML = 'Отправить';
            isSending = false;
        }
    };

    function startResultPolling(requestId) {
        console.log('Start polling for request:', requestId);
        let pollCount = 0;
        const maxPolls = 120;
        const pollInterval = setInterval(async () => {
            pollCount++;
            try {
                const response = await fetch(`/api/queue/result/${requestId}`);
                const data = await response.json();
                if (data.status === 'completed') {
                    clearInterval(pollInterval);
                    if (data.result) {
                        const resultSessionId = data.result.session_id || pendingRequests[requestId]?.sessionId;
                        if (data.result.error) {
                            if (resultSessionId === currentSessionId) {
                                displayMessage('assistant', `⚠️ ${data.result.error}`, null, null, null,
                                    data.result.assistant_timestamp || new Date().toISOString(), data.result.response_time, 'system');
                                delete stableSessionStatus[resultSessionId];
                            } else if (resultSessionId) {
                                // Статус ошибки будет виден через fetchQueueStatus
                            }
                            lastCompletionTime[resultSessionId] = Date.now() + 5000;
                        } else if (data.result.messages) {
                            for (const msg of data.result.messages) {
                                displayMessage('assistant', msg.response, msg.generated_image, msg.file_type, msg.file_name,
                                    msg.assistant_timestamp, msg.response_time, msg.model_used);
                            }
                        } else if (data.result.response) {
                            let responseTime = data.result.response_time;
                            let modelUsed = data.result.model_used;
                            const isError = data.result.is_error || false;
                            if (data.result.mm_time && data.result.gen_time) {
                                responseTime = { mm_time: data.result.mm_time, gen_time: data.result.gen_time, mm_model: data.result.mm_model, gen_model: data.result.gen_model };
                                modelUsed = data.result.gen_model;
                            } else if (typeof responseTime === 'string' && responseTime.startsWith('{')) {
                                try { responseTime = JSON.parse(responseTime); } catch (e) {}
                            }
                            if (resultSessionId === currentSessionId) {
                                displayMessage('assistant', data.result.response, data.result.generated_image,
                                    data.result.file_type, data.result.file_name,
                                    data.result.assistant_timestamp || new Date().toISOString(), responseTime, modelUsed);
                                delete stableSessionStatus[resultSessionId];
                                updateLastVisit(currentSessionId);
                            } else {
                                setNewMessageIndicator(resultSessionId, true);
                                delete stableSessionStatus[resultSessionId];
                            }
                            lastCompletionTime[resultSessionId] = Date.now() + 5000;
                        }
                    }
                    delete pendingRequests[requestId];
                    window.updateStatusCounter();
                    fetchQueueStatus();
                    setTimeout(() => loadSessionsFromServer(), 500);
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    const resultSessionId = data.result?.session_id || pendingRequests[requestId]?.sessionId;
                    if (resultSessionId === currentSessionId) {
                        displayMessage('assistant', `⚠️ Ошибка: ${data.error || 'Неизвестная ошибка'}`, null, null, null,
                            data.result?.assistant_timestamp || new Date().toISOString(), data.result?.response_time, 'system');
                        delete stableSessionStatus[resultSessionId];
                    } else if (resultSessionId) {
                        // Статус ошибки будет виден через fetchQueueStatus
                    }
                    lastCompletionTime[resultSessionId] = Date.now() + 5000;
                    delete pendingRequests[requestId];
                    window.updateStatusCounter();
                    fetchQueueStatus();
                } else if (data.status === 'pending') {
                    // Статус обновится через fetchQueueStatus
                }
                if (pollCount >= maxPolls) {
                    clearInterval(pollInterval);
                    displayMessage('assistant', '⚠️ Превышено время ожидания ответа. Проверьте статус запроса в панели "Мои запросы".',
                        null, null, null, new Date().toISOString(), null, 'system');
                    delete stableSessionStatus[currentSessionId];
                    delete pendingRequests[requestId];
                }
            } catch (error) {
                console.error('Error polling result:', error);
                if (pollCount >= maxPolls) clearInterval(pollInterval);
            }
        }, 3000);
    }

    if (tempAttachedFile) {
        const reader = new FileReader();
        reader.onload = async function(e) {
            fileData = e.target.result.split(',')[1];
            fileType = tempAttachedFile.type;
            fileName = tempAttachedFile.name;
            displayUserMessage(fileData, fileType, fileName);
            await sendToServer();
        };
        reader.readAsDataURL(tempAttachedFile);
    } else {
        displayUserMessage(null, null, null);
        await sendToServer();
    }
}

// -------------------------------
// Удаление сеанса
// -------------------------------
function deleteSession(sessionId) {
    for (let [id, req] of Object.entries(pendingRequests)) {
        if (req.sessionId === sessionId && !req.processed) {
            pendingRequests[id].processed = true;
        }
    }
    delete newMessageIndicators[sessionId];
    delete stableSessionStatus[sessionId];
    delete lastCompletionTime[sessionId];

    fetch(`/api/sessions/${sessionId}/delete`, { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') {
                delete sessionsData[sessionId];
                const sessionItem = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
                if (sessionItem) sessionItem.remove();
                const sessionsCount = document.querySelectorAll('.session-item').length;
                document.getElementById('sessions-count').textContent = sessionsCount;
                if (sessionId === currentSessionId) {
                    const remainingSessions = document.querySelectorAll('.session-item');
                    if (remainingSessions.length > 0) {
                        switchSession(remainingSessions[0].dataset.sessionId);
                    } else {
                        setTimeout(() => createNewSession(), 50);
                    }
                }
            }
        })
        .catch(err => alert('Ошибка при удалении сеанса: ' + err.message));
}

// -------------------------------
// Сохранение чата как HTML
// -------------------------------
async function saveChatAsHTML() {
    // Получаем текст подвала
    let footerText = "";
    try {
        const response = await fetch('/api/footer-text');
        if (response.ok) footerText = await response.text();
        else footerText = "Подпись не настроена";
    } catch (error) {
        console.error('Ошибка получения подписи:', error);
        footerText = "Ошибка загрузки подписи";
    }

    // Активный сеанс
    const activeSession = document.querySelector('.session-item.active');
    if (!activeSession) {
        alert('Нет активного сеанса для сохранения');
        return;
    }

    // Заголовок сеанса
    const title = activeSession.querySelector('.session-title')?.textContent || 'Чат';
    const now = new Date();
    const timestamp = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;

    // Разделяем подпись на две строки, если есть (с)
    let footerLine1 = footerText, footerLine2 = '';
    if (footerText.includes('(с)')) {
        const parts = footerText.split('(с)', 1);
        footerLine1 = parts[0].trim();
        footerLine2 = '(с)' + footerText.split('(с)')[1].trim();
    }

    // Собираем сообщения
    const messages = [];
    document.querySelectorAll('.user-message, .assistant-message, .bot-message').forEach(msgEl => {
        const role = msgEl.classList.contains('user-message') ? 'user' : 'assistant';
        const timestamp = msgEl.dataset.timestamp;
        const headerEl = msgEl.querySelector('.message-header');
        let timeHtml = headerEl ? headerEl.innerHTML : formatFullDateTime(timestamp);
        const contentEl = msgEl.querySelector('.message-content');
        let contentHtml = contentEl ? contentEl.innerHTML : '';
        let fileHtml = '';
        const imageEl = msgEl.querySelector('.attached-image');
        if (imageEl) fileHtml += `<div class="image-container">${imageEl.outerHTML}</div>`;
        const audioEl = msgEl.querySelector('audio');
        if (audioEl && !imageEl) fileHtml += `<div class="audio-container">${audioEl.outerHTML}</div>`;
        const fileEl = msgEl.querySelector('.attached-file');
        if (fileEl && !imageEl && !audioEl) fileHtml += `<div class="file-container">${fileEl.outerHTML}</div>`;
        messages.push({ role, timestamp, timeHtml, contentHtml, fileHtml });
    });

    if (messages.length === 0) {
        alert('Нет сообщений для сохранения');
        return;
    }

    // Загружаем актуальные CSS-файлы
    let styleContent = '';
    let exportStyleContent = '';
    try {
        const styleResponse = await fetch('/static/style.css');
        styleContent = await styleResponse.text();
    } catch (e) {
        console.error('Не удалось загрузить style.css', e);
    }
    try {
        const exportResponse = await fetch('/static/export.css');
        exportStyleContent = await exportResponse.text();
    } catch (e) {
        console.error('Не удалось загрузить export.css', e);
    }

    // Удаляем @import из export.css (если он там есть)
    exportStyleContent = exportStyleContent.replace(/@import\s+url\(['"]?style\.css['"]?\);?\s*/g, '');

    // Объединяем стили
    const combinedStyles = styleContent + '\n' + exportStyleContent;

    // Получаем заголовок сайта из живой страницы
    const siteTitle = document.querySelector('header h1')?.textContent || 'ПЛИИ';

    // Формируем HTML
    const html = `<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>${escapeHtml(title)} - Сохраненный чат</title>
    <style>${combinedStyles}</style>
</head>
<body>
    <header>
        <h1>${escapeHtml(siteTitle)}</h1>
    </header>
    <main>
        <div class="chat-wrapper">
            <div class="chat-header">
                <h1>Сеанс: ${escapeHtml(title)}</h1>
                <p>📅 Сохранено: ${now.toLocaleString('ru-RU', { day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit' })}</p>
                <p>💬 Всего сообщений: ${messages.length}</p>
            </div>
            <div class="chat-messages">
                ${messages.map(msg => `
                    <div class="${msg.role === 'user' ? 'user-message' : 'assistant-message'}">
                        <small class="message-time">${msg.timeHtml}</small>
                        <div class="message-content">${msg.contentHtml}</div>
                        ${msg.fileHtml}
                    </div>
                `).join('')}
            </div>
        </div>
    </main>
    <footer>
        <div class="footer-content">
            <div class="footer-line1">${escapeHtml(footerLine1)}</div>
            ${footerLine2 ? `<div class="footer-line2">${escapeHtml(footerLine2)}</div>` : ''}
        </div>
    </footer>
</body>
</html>`;

    const blob = new Blob([html], {type: 'text/html;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat_${timestamp}.html`;
    a.click();
    URL.revokeObjectURL(url);
}

// -------------------------------
// Копирование кода
// -------------------------------
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (err) {
        console.error('Ошибка копирования через clipboard API:', err);
        try {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            const success = document.execCommand('copy');
            document.body.removeChild(textarea);
            return success;
        } catch (fallbackErr) {
            console.error('Ошибка копирования через execCommand:', fallbackErr);
            return false;
        }
    }
}

async function handleCopyClick(button, codeElement) {
    const code = codeElement.textContent || codeElement.innerText;
    const originalHTML = button.innerHTML;
    const originalClass = button.className;
    button.innerHTML = '⏳';
    button.disabled = true;
    const success = await copyToClipboard(code);
    if (success) {
        button.innerHTML = '✓';
        button.className = originalClass + ' copied';
        button.title = 'Скопировано!';
        setTimeout(() => {
            button.innerHTML = '📋';
            button.className = originalClass.replace(' copied', '');
            button.title = 'Копировать код';
            button.disabled = false;
        }, 2000);
    } else {
        button.innerHTML = '✗';
        button.title = 'Ошибка копирования';
        setTimeout(() => {
            button.innerHTML = '📋';
            button.title = 'Копировать код';
            button.disabled = false;
        }, 2000);
    }
}

function addCopyButtonsToMessage(messageElement) {
    if (!messageElement) return;
    const codeBlocks = messageElement.querySelectorAll('pre code');
    codeBlocks.forEach((codeBlock) => {
        const parent = codeBlock.parentNode;
        if (parent.classList.contains('code-block-wrapper')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';
        const copyButton = document.createElement('button');
        copyButton.className = 'copy-code-button';
        copyButton.innerHTML = '📋';
        copyButton.title = 'Копировать код';
        copyButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            handleCopyClick(copyButton, codeBlock);
        });
        parent.parentNode.insertBefore(wrapper, parent);
        wrapper.appendChild(parent);
        wrapper.appendChild(copyButton);
    });

    const contentDiv = messageElement.querySelector('.message-content');
    if (contentDiv && !contentDiv.querySelector('.copy-transcript-button')) {
        const text = contentDiv.innerText || contentDiv.textContent;
        if (text.includes('🎤 Распознано:')) {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'copy-transcript-button';
            copyBtn.innerHTML = '📋';
            copyBtn.title = 'Копировать текст';
            copyBtn.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                const textToCopy = text.replace('🎤 Распознано:', '').trim();
                copyToClipboard(textToCopy);
                copyBtn.innerHTML = '✓';
                setTimeout(() => copyBtn.innerHTML = '📋', 2000);
            };
            contentDiv.style.position = 'relative';
            contentDiv.appendChild(copyBtn);
        }
    }
}

function setupCopyButtonsObserver() {
    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach((node) => {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    if (node.classList && (node.classList.contains('user-message') || node.classList.contains('assistant-message') || node.classList.contains('bot-message'))) {
                        addCopyButtonsToMessage(node);
                    }
                    const messages = node.querySelectorAll?.('.user-message, .assistant-message, .bot-message');
                    if (messages) messages.forEach(addCopyButtonsToMessage);
                }
            });
        });
    });
    observer.observe(chatMessages, { childList: true, subtree: true });
}

// Сохраняем оригинальные функции для переопределения
const originalLoadMessages = loadMessages;
window.loadMessages = function(sessionId) {
    return originalLoadMessages(sessionId).then(() => {
        setTimeout(addCopyButtonsToAllCodeBlocks, 100);
    });
};

const originalDisplayMessage = displayMessage;
window.displayMessage = function(role, content, fileData, fileType, fileName, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel) {
    const result = originalDisplayMessage.call(this, role, content, fileData, fileType, fileName, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel);
    const messages = document.getElementById('chat-messages');
    if (messages) {
        const lastMessage = messages.lastElementChild;
        if (lastMessage) setTimeout(() => addCopyButtonsToMessage(lastMessage), 50);
    }
    return result;
};

function addCopyButtonsToAllCodeBlocks() {
    document.querySelectorAll('.user-message, .assistant-message, .bot-message').forEach(addCopyButtonsToMessage);
}

document.addEventListener('DOMContentLoaded', function() {
    setTimeout(setupCopyButtonsObserver, 500);
    setTimeout(addCopyButtonsToAllCodeBlocks, 1000);
});