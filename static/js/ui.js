// Функции, обновляющие DOM
import { appState, setNewMessageIndicator } from './state.js';
import { formatFullDateTime, formatFileSize, escapeHtml, decodeHtmlEntities } from './utils.js';
import * as api from './api.js';

// Отображение одного сообщения
export function displayMessage(role, content, fileData, fileType, fileName, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel) {
    const container = document.getElementById('chat-messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = role === 'user' ? 'user-message' : 'assistant-message bot-message';
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

    // Информация о файле для пользователя
    if (role === 'user' && fileName && fileData) {
        const fileSize = formatFileSize(Math.round((fileData.length * 3) / 4));
        timeDisplay += ` <span class="file-info">[📎 ${fileName}, ${fileSize}]</span>`;
        if (fileType && fileType.startsWith('image/')) {
            timeDisplay += ` <a href="data:${fileType};base64,${fileData}" download="${fileName}" class="download-link-inline" onclick="event.stopPropagation()">⬇️</a>`;
        }
    }

    // Информация о файле для ассистента
    if (role === 'assistant' && fileName && fileData) {
        const fileSize = formatFileSize(Math.round((fileData.length * 3) / 4));
        timeDisplay += ` <span class="file-info">[📎 ${fileName}, ${fileSize}]</span>`;
        if (fileType && fileType.startsWith('image/')) {
            timeDisplay += ` <a href="data:${fileType};base64,${fileData}" download="${fileName}" class="download-link-inline" onclick="event.stopPropagation()">⬇️</a>`;
        }
    }

    // Информация о модели для ассистента
    if (role === 'assistant') {
        const modelInfoParts = [];
        if (responseTime) {
            if (typeof responseTime === 'object') {
                if (responseTime.mm_time && responseTime.gen_time) {
                    modelInfoParts.push(`<span class="model-response-info">Обработано: ${responseTime.mm_model || 'неизвестно'} за ${parseFloat(responseTime.mm_time).toFixed(1)} сек</span>`);
                    modelInfoParts.push(`<span class="model-response-info">Сгенерировано: ${responseTime.gen_model || 'неизвестно'} за ${parseFloat(responseTime.gen_time).toFixed(1)} сек</span>`);
                } else if (responseTime.mm_time) {
                    modelInfoParts.push(`<span class="model-response-info">Обработано: ${responseTime.mm_model || 'неизвестно'} за ${parseFloat(responseTime.mm_time).toFixed(1)} сек</span>`);
                } else if (responseTime.gen_time) {
                    modelInfoParts.push(`<span class="model-response-info">Сгенерировано: ${responseTime.gen_model || 'неизвестно'} за ${parseFloat(responseTime.gen_time).toFixed(1)} сек</span>`);
                }
            } else if (typeof responseTime === 'number' || !isNaN(parseFloat(responseTime))) {
                const timeValue = typeof responseTime === 'number' ? responseTime : parseFloat(responseTime);
                const fullModelName = modelName || appState.defaultModelName;
                if (timeValue > 0) {
                    modelInfoParts.push(`<span class="model-response-info">Ответ: ${fullModelName} за ${timeValue.toFixed(1)} сек</span>`);
                } else {
                    modelInfoParts.push(`<span class="model-response-info">Модель: ${fullModelName}</span>`);
                }
            }
        } else {
            modelInfoParts.push(`<span class="model-response-info">Модель: ${modelName || appState.defaultModelName}</span>`);
        }
        if (modelInfoParts.length > 0) {
            timeDisplay += `<div class="model-info-container">${modelInfoParts.join('')}</div>`;
        }
    }

    let html = `<small class="message-metadata">${timeDisplay}</small>`;

    // Текст сообщения
    if (typeof content === 'string') {
        let textContent = content;
        if (content.startsWith('[')) {
            try {
                const parts = JSON.parse(content);
                textContent = parts.map(p => p.type === 'text' ? p.text : '').join('\n').trim();
            } catch { /* не JSON */ }
        }
        const decoded = role === 'assistant' ? decodeHtmlEntities(textContent) : textContent;
        html += `<div class="message-content">${marked.parse(escapeHtml(decoded))}</div>`;
    }

    // Вложения
    if (fileData) {
        if (fileType && fileType.startsWith('image/')) {
            html += `<div class="image-container"><img src="data:${fileType};base64,${fileData}" class="attached-image" alt="${fileName}" onclick="openImageModal(this.src, '${fileName}')"></div>`;
        } else if (fileType && fileType.startsWith('audio/')) {
            html += `<audio controls src="data:${fileType};base64,${fileData}"></audio>`;
        } else {
            html += `<div class="attached-file"><span class="file-icon">📄</span><a href="data:${fileType};base64,${fileData}" download="${fileName}">${fileName}</a></div>`;
        }
    }

    msgDiv.innerHTML = html;
    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;

    updateMessageCount();
    setTimeout(() => addCopyButtonsToMessage(msgDiv), 50);
}

// Обновление счётчика сообщений
export function updateMessageCount() {
    const count = document.querySelectorAll('.user-message, .assistant-message').length;
    document.getElementById('context-info').textContent = `Сообщений: ${count}`;
}

// Обновление списка сеансов
export function updateSessionsList(sessions) {
    const sessionsList = document.getElementById('sessions-list');
    const currentActiveId = appState.currentSessionId;
    
    sessions.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
    
    let html = '';
    sessions.forEach(s => {
        const isActive = s.id === currentActiveId ? 'active' : '';
        const dateStr = s.updated_at ? s.updated_at.replace('T', ' ').substring(0, 19) : '';
        const indicatorData = appState.newMessageIndicators[s.id];
        const hasIndicator = indicatorData && indicatorData.hasNew && s.id !== currentActiveId;
        const isError = indicatorData && indicatorData.isError;
        
        html += `
            <div class="session-item ${isActive}" data-session-id="${s.id}">
                <div class="session-content">
                    <div class="session-info">
                        <div class="session-title">${escapeHtml(s.title)}${hasIndicator ? (isError ? '<span class="error-message-indicator">●</span>' : '<span class="new-message-indicator">●</span>') : ''}</div>
                        <div class="session-date">${dateStr}</div>
                    </div>
                    <button class="delete-session-button" title="Удалить сеанс">🗑️</button>
                </div>
            </div>
        `;
    });
    
    sessionsList.innerHTML = html;
    document.getElementById('sessions-count').textContent = sessions.length;
    
    // Перепривязываем обработчики
    attachSessionEventHandlers();
}

// Привязка обработчиков к сеансам
function attachSessionEventHandlers() {
    document.querySelectorAll('.session-item').forEach(el => {
        el.addEventListener('click', function(e) {
            if (e.target.closest('.delete-session-button')) return;
            const sessionId = this.dataset.sessionId;
            if (sessionId === appState.currentSessionId) return;
            setNewMessageIndicator(sessionId, false);
            window.location.hash = `#session-${sessionId}`; // для возможности навигации
            // Здесь должен быть вызов переключения сеанса
        });
    });
    
    document.querySelectorAll('.delete-session-button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const sessionItem = this.closest('.session-item');
            const sessionId = sessionItem.dataset.sessionId;
            const sessionTitle = sessionItem.querySelector('.session-title').textContent;
            if (confirm(`Удалить сеанс "${sessionTitle}"?`)) {
                // Здесь вызов удаления
            }
        });
    });
}

// Добавление кнопок копирования кода
function addCopyButtonsToMessage(messageElement) {
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
        
        copyButton.addEventListener('click', async (e) => {
            e.preventDefault();
            const code = codeBlock.textContent || codeBlock.innerText;
            try {
                await navigator.clipboard.writeText(code);
                copyButton.innerHTML = '✓';
                copyButton.classList.add('copied');
                setTimeout(() => {
                    copyButton.innerHTML = '📋';
                    copyButton.classList.remove('copied');
                }, 2000);
            } catch (err) {
                copyButton.innerHTML = '✗';
                setTimeout(() => copyButton.innerHTML = '📋', 2000);
            }
        });
        
        parent.parentNode.insertBefore(wrapper, parent);
        wrapper.appendChild(parent);
        wrapper.appendChild(copyButton);
    });
}

// Функции для модального окна
export function openImageModal(src, alt) {
    const modal = document.getElementById('image-modal');
    const modalImg = document.getElementById('modal-image');
    const captionText = document.getElementById('modal-caption');
    modal.style.display = "block";
    modalImg.src = src;
    captionText.innerHTML = alt;
}

export function closeImageModal() {
    document.getElementById('image-modal').style.display = 'none';
}

// Обновление индикаторов новых сообщений в DOM
export function updateIndicatorsInDOM() {
    document.querySelectorAll('.session-item').forEach(el => {
        const sessionId = el.dataset.sessionId;
        const titleElement = el.querySelector('.session-title');
        let indicator = el.querySelector('.new-message-indicator');
        let errorIndicator = el.querySelector('.error-message-indicator');
        if (indicator) indicator.remove();
        if (errorIndicator) errorIndicator.remove();

        const indicatorData = appState.newMessageIndicators[sessionId];
        if (indicatorData && indicatorData.hasNew && sessionId !== appState.currentSessionId) {
            const span = document.createElement('span');
            span.className = indicatorData.isError ? 'error-message-indicator' : 'new-message-indicator';
            span.textContent = '●';
            titleElement.appendChild(span);
        }
    });
}