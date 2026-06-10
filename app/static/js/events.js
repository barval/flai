// app/static/js/events.js
// Server-Sent Events (SSE) — replaces HTTP polling for real-time updates

let eventSource = null;
let reconnectTimer = null;
let pendingRequestIds = {};  // requestId -> { sessionId, timestamp }

function connectEventStream() {
    if (eventSource) {
        eventSource.close();
    }

    // Restore pending requests from sessionStorage (survives session switches)
    restorePendingRequests();

    eventSource = new EventSource('/api/events/stream');

    eventSource.addEventListener('connected', function () {
        dlog('SSE connected');
    });

    eventSource.onmessage = function (e) {
        try {
            const event = JSON.parse(e.data);
            handleEvent(event);
        } catch (err) {
            dlog('SSE parse error:', err);
        }
    };

    eventSource.onerror = function () {
        dlog('SSE connection error, reconnecting...');
        eventSource.close();
        scheduleReconnect();
    };
}

function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(function () {
        connectEventStream();
        // Full sync after reconnect — may have missed events
        if (typeof loadSessionsFromServer === 'function') {
            loadSessionsFromServer();
        }
        if (typeof fetchQueueStatus === 'function') {
            fetchQueueStatus();
        }
        if (typeof restoreTaskProgress === 'function') {
            restoreTaskProgress();
        }
        if (typeof loadDocuments === 'function') {
            loadDocuments(false);
        }
        // Check if any pending tasks completed while SSE was disconnected
        verifyPendingRequests();
    }, 3000);
}

function disconnectEventStream() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
}

function handleEvent(event) {
    switch (event.type) {
        case 'result_completed':
            onResultCompleted(event.data);
            break;
        case 'stream_token':
            onStreamToken(event.data);
            break;
        case 'stream_cancelled':
            onStreamCancelled(event.data);
            break;
        case 'camera_image':
            onCameraImage(event.data);
            break;
        case 'task_progress':
            onTaskProgress(event.data);
            break;
        case 'video_step':
            onVideoStep(event.data);
            break;
        case 'image_step':
            onImageStep(event.data);
            break;
        case 'image_preview':
            onImagePreview(event.data);
            break;
        case 'message_new':
            onMessageNew(event.data);
            break;
        case 'document_indexed':
            if (typeof loadDocuments === 'function') {
                loadDocuments(false);
            }
            break;
        default:
            dlog('SSE unknown event type:', event.type);
    }
}

// ── camera_image ────────────────────────────────────────────────────

function onCameraImage(data) {
    if (!data || !data.session_id || data.session_id !== currentSessionId) return;
    if (data.message_id && displayedMessageIds.has(data.message_id)) return;
    dlog('onCameraImage:', data.message_id);
    window.displayMessage('assistant', data.response, null,
        data.file_type || null, data.file_name || null, data.file_path || null,
        data.assistant_timestamp || new Date().toISOString(),
        data.response_time, data.model_used,
        null, null, null, null, data.message_id,
        data.response_style);
}

// ── task_progress ────────────────────────────────────────────────────

const STAGE_LABELS = {
    preparing_gpu: '⏳ Очистка GPU...',
    analyzing: '🔍 Анализ запроса...',
    analyzing_image: '🔍 Анализ изображения...',
    analyzing_prompt: '🔍 Анализ промпта...',
    generating_video: '🎬 Генерация видео...',
    generating_image: '🎨 Генерация изображения...',
    editing_image: '✏️ Редактирование изображения...',
    loading_reasoning_model: '🧠 Загрузка модели рассуждений...',
    capturing_snapshot: '📹 Получение снимка...',
};

function onTaskProgress(data) {
    if (!data || !data.session_id || data.session_id !== currentSessionId) return;
    if (!data.stage) return;
    dlog('onTaskProgress:', data.stage);

    const label = STAGE_LABELS[data.stage] || data.stage;
    _updateProgressElement(data.task_id, label);
}

function _updateProgressElement(taskId, text) {
    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    let progressEl = chatMessages.querySelector('.task-progress[data-task-id="' + taskId + '"]');
    if (!progressEl) {
        progressEl = document.createElement('div');
        progressEl.className = 'task-progress';
        progressEl.setAttribute('data-task-id', taskId);
        chatMessages.appendChild(progressEl);
    }
    progressEl.textContent = text;
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function _removeProgressElement(taskId) {
    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;
    const el = chatMessages.querySelector('.task-progress[data-task-id="' + taskId + '"]');
    if (el) el.remove();
}

// ── video_step ───────────────────────────────────────────────────────

function onVideoStep(data) {
    if (!data || !data.session_id || data.session_id !== currentSessionId) return;
    if (!data.task_id || data.total === undefined) return;
    dlog('onVideoStep:', data.step, '/', data.total);

    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    const pct = data.percent || Math.round((data.step / data.total) * 100);

    let barContainer = chatMessages.querySelector('.video-progress-bar[data-task-id="' + data.task_id + '"]');
    if (!barContainer) {
        barContainer = document.createElement('div');
        barContainer.className = 'video-progress-bar';
        barContainer.setAttribute('data-task-id', data.task_id);
        barContainer.innerHTML = '<div class="fill"></div><span class="label"></span>';
        chatMessages.appendChild(barContainer);
    }

    barContainer.querySelector('.fill').style.width = pct + '%';
    barContainer.querySelector('.label').textContent = '🎬 ' + data.step + '/' + data.total + ' (' + pct + '%)';

    _removeProgressElement(data.task_id);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ── image_step ────────────────────────────────────────────────────

function onImageStep(data) {
    if (!data || !data.session_id || data.session_id !== currentSessionId) return;
    if (!data.task_id || data.total === undefined) return;
    dlog('onImageStep:', data.step, '/', data.total);

    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    const pct = data.percent || Math.round((data.step / data.total) * 100);

    let barContainer = chatMessages.querySelector('.video-progress-bar[data-task-id="' + data.task_id + '"]');
    if (!barContainer) {
        barContainer = document.createElement('div');
        barContainer.className = 'video-progress-bar';
        barContainer.setAttribute('data-task-id', data.task_id);
        barContainer.innerHTML = '<div class="fill"></div><span class="label"></span>';
        chatMessages.appendChild(barContainer);
    }

    barContainer.querySelector('.fill').style.width = pct + '%';
    barContainer.querySelector('.label').textContent = '🎨 ' + data.step + '/' + data.total + ' (' + pct + '%)';

    _removeProgressElement(data.task_id);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ── image_preview ────────────────────────────────────────────────────

function onImagePreview(data) {
    if (!data || !data.session_id || data.session_id !== currentSessionId) return;
    if (!data.task_id || !data.image_b64) return;
    dlog('onImagePreview:', data.task_id, 'step:', data.step);

    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    let previewContainer = chatMessages.querySelector('.image-preview-container[data-task-id="' + data.task_id + '"]');
    if (!previewContainer) {
        previewContainer = document.createElement('div');
        previewContainer.className = 'image-preview-container';
        previewContainer.setAttribute('data-task-id', data.task_id);
        previewContainer.innerHTML = '<img alt="preview"><div class="step-label"></div>';
        chatMessages.appendChild(previewContainer);
    }

    previewContainer.querySelector('img').src = 'data:image/png;base64,' + data.image_b64;
    if (data.total !== undefined) {
        previewContainer.querySelector('.step-label').textContent = 'Шаг ' + data.step + '/' + data.total;
    }

    _removeProgressElement(data.task_id);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ── thinking tag filter (fallback for --reasoning_format none) ─────────
// Handles two formats:
// - `` blocks (Qwen, DeepSeek, Gemma, QwQ)
// - `<|channel|>analysis<|message|>...<|end|>` (gpt-oss-20b ChatML reasoning)

function _stripThinkingTags(text) {
    if (!text) return text;
    if (!text.includes('<think') && !text.includes('<|channel|>')) return text;
    let result = text;
    // Remove complete <think>...</think> blocks (including multiline)
    result = result.replace(/<think[\s>][\s\S]*?<\/think>/gi, '');
    // Remove incomplete opening tag at the end (streaming: closing tag hasn't arrived yet)
    result = result.replace(/<think[\s>][\s\S]*$/i, '');
    // Remove complete <|channel|>analysis<|message|>...<|end|> blocks
    result = result.replace(/<\|channel\|>analysis<\|message\|>[\s\S]*?<\|end\|>/gi, '');
    // Remove incomplete opening tag at the end (streaming)
    result = result.replace(/<\|channel\|>analysis<\|message\|>[\s\S]*$/i, '');
    return result;
}

// ── stream_token ─────────────────────────────────────────────────────

function onStreamToken(data) {
    if (!data || !data.task_id || !data.token) return;
    dlog('onStreamToken:', data.task_id, 'token len:', data.token.length);

    let reqInfo = pendingRequestIds[data.task_id];
    if (!reqInfo) {
        // After page refresh, pendingRequestIds is empty — create entry on first token
        reqInfo = {
            sessionId: data.session_id,
            timestamp: Date.now(),
            accumulatedContent: '',
            streamStartTime: Date.now(),
            lastSpeedUpdate: Date.now()
        };
        pendingRequestIds[data.task_id] = reqInfo;
    }

    // Accumulate content
    if (!reqInfo.accumulatedContent) {
        reqInfo.accumulatedContent = '';
        reqInfo.streamStartTime = Date.now();
        reqInfo.lastSpeedUpdate = Date.now();
    }
    reqInfo.accumulatedContent += data.token;

    // Save to sessionStorage for recovery after page reload
    _saveStreamToSessionStorage(data.task_id, data.session_id, reqInfo.accumulatedContent);

    // Only update DOM for active session
    if (data.session_id !== currentSessionId) return;

    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    let streamMsg = chatMessages.querySelector('.assistant-message[data-streaming="true"]');
    let indicatorSpan = null;

    if (!streamMsg) {
        // Create new message element with proper structure
        streamMsg = document.createElement('div');
        streamMsg.className = 'assistant-message bot-message';
        streamMsg.setAttribute('data-streaming', 'true');
        streamMsg.setAttribute('data-task-id', data.task_id);
        streamMsg.setAttribute('data-role', 'assistant');
        streamMsg.dataset.sessionId = data.session_id;
        chatMessages.appendChild(streamMsg);

        // Header with timestamp, streaming indicator, cancel button
        const headerDiv = document.createElement('span');
        headerDiv.className = 'message-header';

        const timeSpan = document.createElement('span');
        timeSpan.textContent = '📅 ' + formatFullDateTime(new Date().toISOString());
        headerDiv.appendChild(timeSpan);

        indicatorSpan = document.createElement('span');
        indicatorSpan.className = 'streaming-indicator blink';
        indicatorSpan.textContent = '⚡ ' + t('generating');
        headerDiv.appendChild(indicatorSpan);

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'cancel-stream-button';
        cancelBtn.title = t('stop_generating');
        cancelBtn.textContent = '■';
        cancelBtn.addEventListener('click', function () {
            cancelBtn.disabled = true;
            cancelBtn.textContent = '⏳';
            cancelBtn.title = t('cancelling');
            fetchWithCSRF('/api/cancel_task/' + data.task_id, { method: 'POST' }).catch(function () {});
        });
        headerDiv.appendChild(cancelBtn);

        streamMsg.appendChild(headerDiv);

        // Content div
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        streamMsg.appendChild(contentDiv);
    }

    // Update content (strip thinking tags for display)
    const contentDiv = streamMsg.querySelector('.message-content');
    if (contentDiv) {
        contentDiv.textContent = _stripThinkingTags(reqInfo.accumulatedContent);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Live token/s estimate (every 500ms)
    var now = Date.now();
    if (!indicatorSpan) {
        indicatorSpan = streamMsg.querySelector('.streaming-indicator');
    }
    if (indicatorSpan && now - reqInfo.lastSpeedUpdate >= 500) {
        reqInfo.lastSpeedUpdate = now;
        var elapsed = (now - reqInfo.streamStartTime) / 1000;
        if (elapsed > 0.5) {
            var estTokens = Math.round(reqInfo.accumulatedContent.length / 3);
            var tps = (estTokens / elapsed).toFixed(1);
            indicatorSpan.textContent = '⚡ ' + t('generating') + ' (' + tps + t('tps_suffix') + ')';
        }
    }
}

function _saveStreamToSessionStorage(taskId, sessionId, content) {
    try {
        sessionStorage.setItem('stream_' + taskId, JSON.stringify({
            sessionId: sessionId,
            content: content,
            timestamp: Date.now()
        }));
    } catch (e) {
        // sessionStorage full or unavailable — ignore
    }
}

function _clearStreamFromSessionStorage(taskId) {
    try {
        sessionStorage.removeItem('stream_' + taskId);
    } catch (e) {
        // ignore
    }
}

function restoreStreamingFromSessionStorage() {
    if (!currentSessionId) return;
    const keysToRemove = [];
    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    for (var i = 0; i < sessionStorage.length; i++) {
        var key = sessionStorage.key(i);
        if (!key || !key.startsWith('stream_')) continue;
        try {
            var saved = JSON.parse(sessionStorage.getItem(key));
            if (!saved || !saved.sessionId || !saved.content) {
                keysToRemove.push(key);
                continue;
            }
            if (saved.sessionId !== currentSessionId) {
                keysToRemove.push(key);
                continue;
            }
            // Restore DOM for this session
            var existing = chatMessages.querySelector('[data-task-id="' + key.replace('stream_', '') + '"]');
            if (existing) {
                keysToRemove.push(key);
                continue;
            }
            var taskId = key.replace('stream_', '');
            var msgDiv = document.createElement('div');
            msgDiv.className = 'assistant-message bot-message';
            msgDiv.setAttribute('data-streaming', 'true');
            msgDiv.setAttribute('data-task-id', taskId);
            msgDiv.setAttribute('data-role', 'assistant');
            msgDiv.dataset.sessionId = currentSessionId;
            chatMessages.appendChild(msgDiv);

            var headerDiv = document.createElement('span');
            headerDiv.className = 'message-header';

            var timeSpan = document.createElement('span');
            timeSpan.textContent = '📅 ' + formatFullDateTime(new Date(saved.timestamp).toISOString());
            headerDiv.appendChild(timeSpan);

            var indicatorSpan = document.createElement('span');
            indicatorSpan.className = 'streaming-indicator blink';
            indicatorSpan.textContent = '⚡ ' + t('generating');
            headerDiv.appendChild(indicatorSpan);

            var cancelBtn = document.createElement('button');
            cancelBtn.className = 'cancel-stream-button';
            cancelBtn.title = t('stop_generating');
            cancelBtn.textContent = '■';
            cancelBtn.addEventListener('click', function () {
                cancelBtn.disabled = true;
                cancelBtn.textContent = '⏳';
                cancelBtn.title = t('cancelling');
                fetchWithCSRF('/api/cancel_task/' + taskId, { method: 'POST' }).catch(function () {});
            });
            headerDiv.appendChild(cancelBtn);

            msgDiv.appendChild(headerDiv);

            var contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.textContent = saved.content;
            msgDiv.appendChild(contentDiv);

            // Re-register in pendingRequestIds
            if (!pendingRequestIds[taskId]) {
                pendingRequestIds[taskId] = {
                    sessionId: currentSessionId,
                    timestamp: saved.timestamp,
                    accumulatedContent: saved.content,
                    streamStartTime: Date.now(),
                    lastSpeedUpdate: Date.now()
                };
            }
        } catch (e) {
            keysToRemove.push(key);
        }
    }
    // Clean stale keys
    for (var j = 0; j < keysToRemove.length; j++) {
        try { sessionStorage.removeItem(keysToRemove[j]); } catch (e) {}
    }
}

// ── result_completed ─────────────────────────────────────────────────

function onResultCompleted(data) {
    if (!data || !data.task_id) return;
    dlog('onResultCompleted:', data.task_id, data.status);

    // Clean up progress indicators
    _removeProgressElement(data.task_id);
    const barEl = document.querySelector('.video-progress-bar[data-task-id="' + data.task_id + '"]');
    if (barEl) barEl.remove();
    const previewEl = document.querySelector('.image-preview-container[data-task-id="' + data.task_id + '"]');
    if (previewEl) previewEl.remove();

    const reqInfo = pendingRequestIds[data.task_id];
    if (!reqInfo) {
        // After page refresh, pendingRequestIds is empty (in-memory, not persisted).
        // The result may still carry a valid response — display it and clean up.
        const existingMsg = document.querySelector('[data-task-id="' + data.task_id + '"]');
        if (existingMsg && existingMsg.hasAttribute('data-streaming')) existingMsg.remove();
        _clearStreamFromSessionStorage(data.task_id);
        const sessionId = data.session_id || (data.result && data.result.session_id) || null;
        if (data.status === 'completed' && data.result) {
            handleCompletedResult(data.result, sessionId);
        } else if (data.status === 'error') {
            handleErrorResult(data, sessionId);
        }
        return;
    }

    const expectedSessionId = reqInfo.sessionId;

    // Handle streamed completion differently
    if (reqInfo.accumulatedContent !== undefined) {
        finalizeStreamedMessage(data, reqInfo, expectedSessionId);
        return;
    }

    clearPendingRequest(data.task_id);

    if (data.status === 'completed' && data.result) {
        handleCompletedResult(data.result, expectedSessionId);
    } else if (data.status === 'error') {
        handleErrorResult(data, expectedSessionId);
    }
}

function onStreamCancelled(data) {
    if (!data || !data.task_id) return;
    dlog('onStreamCancelled:', data.task_id);

    // The result_completed will follow shortly — let finalizeStreamedMessage handle cleanup.
    // Just mark the DOM as cancelled.
    const streamMsg = document.querySelector('.assistant-message[data-streaming="true"]');
    if (streamMsg) {
        // Update header: remove cancel button, show cancelled label
        const cancelBtn = streamMsg.querySelector('.cancel-stream-button');
        if (cancelBtn) cancelBtn.remove();
        const indicator = streamMsg.querySelector('.streaming-indicator');
        if (indicator) {
            indicator.className = 'cancelled-label';
            indicator.textContent = '⚠️ ' + t('cancelled');
        }
    }
    _clearStreamFromSessionStorage(data.task_id);
}

function finalizeStreamedMessage(data, reqInfo, expectedSessionId) {
    const resultSessionId = data.result?.session_id || data.session_id || expectedSessionId;
    const taskId = data.task_id || reqInfo.taskId;

    // Update sessions count
    if (resultSessionId && sessionsData[resultSessionId]) {
        sessionsData[resultSessionId].message_count = (sessionsData[resultSessionId].message_count || 0) + 1;
        sessionsData[resultSessionId].updated_at = new Date().toISOString();
        delete newMessageIndicators[resultSessionId];
    }

    // Find stream message by task-id (more reliable than generic [data-streaming])
    const streamMsg = document.querySelector('.assistant-message[data-task-id="' + taskId + '"]');
    if (streamMsg) {
        streamMsg.removeAttribute('data-streaming');
        streamMsg.removeAttribute('data-task-id');
        if (data.result?.message_id) {
            streamMsg.dataset.messageId = data.result.message_id;
            displayedMessageIds.add(data.result.message_id);
        }
        // Remove streaming-only elements
        var cancelBtn = streamMsg.querySelector('.cancel-stream-button');
        if (cancelBtn) cancelBtn.remove();
        var indicator = streamMsg.querySelector('.streaming-indicator');
        if (indicator) indicator.remove();
        var cancelledLabel = streamMsg.querySelector('.cancelled-label');
        if (cancelledLabel) cancelledLabel.remove();

        // Rebuild header with metadata from result
        var result = data.result;
        if (result) {
            var oldHeader = streamMsg.querySelector('.message-header');
            if (oldHeader) {
                var newHeader = document.createElement('span');
                newHeader.className = 'message-header';

                // Timestamp
                var ts = result.assistant_timestamp || new Date().toISOString();
                newHeader.innerHTML = '📅 ' + formatFullDateTime(ts);

                // Model name
                if (result.model_used) {
                    var shortModel = result.model_used.split('/').pop() || result.model_used;
                    var modelSpan = document.createElement('span');
                    modelSpan.className = 'text-muted';
                    modelSpan.textContent = ' | ' + shortModel;
                    newHeader.appendChild(modelSpan);
                }

                // Response time
                if (result.response_time) {
                    var duration = null;
                    if (typeof result.response_time === 'object') {
                        if (result.response_time.mm_time && result.response_time.gen_time) {
                            duration = (parseFloat(result.response_time.mm_time) + parseFloat(result.response_time.gen_time)).toFixed(1);
                        } else if (result.response_time.router && result.response_time.chat) {
                            duration = (parseFloat(result.response_time.router) + parseFloat(result.response_time.chat)).toFixed(1);
                        } else if (result.response_time.mm_time) {
                            duration = parseFloat(result.response_time.mm_time).toFixed(1);
                        } else if (result.response_time.gen_time) {
                            duration = parseFloat(result.response_time.gen_time).toFixed(1);
                        }
                    } else if (typeof result.response_time === 'number' || !isNaN(parseFloat(result.response_time))) {
                        duration = parseFloat(result.response_time).toFixed(1);
                    }
                    if (duration) {
                        var langSuffix = t('seconds_suffix');
                        var timeSpan = document.createElement('span');
                        timeSpan.className = 'text-muted';
                        timeSpan.textContent = ' | ⏱️ ' + duration + langSuffix + ' |';
                        newHeader.appendChild(timeSpan);

                        // Tokens per second
                        if (result.completion_tokens) {
                            var tps = (result.completion_tokens / parseFloat(duration)).toFixed(1);
                            var tpsSpan = document.createElement('span');
                            tpsSpan.className = 'text-muted';
                            tpsSpan.textContent = ' 🚀 ' + tps + t('tps_suffix') + ' |';
                            newHeader.appendChild(tpsSpan);
                        }
                    }
                }

                // Response style emoji
                if (result.response_style) {
                    var styleEmoji = getResponseStyleEmoji(result.response_style);
                    if (styleEmoji) {
                        var styleLabel = t('response_style_' + result.response_style) || result.response_style;
                        var styleSpan = document.createElement('span');
                        styleSpan.className = 'response-style-indicator';
                        styleSpan.title = styleLabel;
                        styleSpan.textContent = styleEmoji;
                        newHeader.appendChild(styleSpan);
                    }
                }

                // TTS button
                var ttsBtn = document.createElement('button');
                ttsBtn.className = 'tts-button';
                ttsBtn.title = t('speak');
                ttsBtn.textContent = '🗣️';
                newHeader.appendChild(ttsBtn);

                // Copy button
                var copyBtn = document.createElement('button');
                copyBtn.className = 'copy-message-button';
                copyBtn.title = t('copy_text');
                copyBtn.textContent = '📋';
                newHeader.appendChild(copyBtn);

                oldHeader.replaceWith(newHeader);

                // Attach TTS click handler
                ttsBtn = newHeader.querySelector('.tts-button');
                if (ttsBtn) {
                    ttsBtn.removeAttribute('onclick');
                    ttsBtn.addEventListener('click', function(e) {
                        e.preventDefault();
                        if (window.IS_RELOADING) return;
                        var fn = window.playTTS || playTTS;
                        if (typeof fn === 'function') fn(ttsBtn, streamMsg);
                    });
                }

                // Attach copy button click handler
                copyBtn = newHeader.querySelector('.copy-message-button');
                if (copyBtn) {
                    copyBtn.addEventListener('click', function(e) {
                        e.preventDefault();
                        if (window.IS_RELOADING) return;
                        var rawText = streamMsg.dataset.rawText || streamMsg.dataset.cleanText || result.response;
                        if (!rawText) return;
                        copyToClipboard(rawText).then(function(success) {
                            var originalHTML = copyBtn.innerHTML;
                            var originalTitle = copyBtn.title;
                            if (success) {
                                copyBtn.innerHTML = '✓';
                                copyBtn.title = t('copied');
                                setTimeout(function() {
                                    copyBtn.innerHTML = originalHTML;
                                    copyBtn.title = originalTitle;
                                }, 2000);
                            }
                        });
                    });
                }
            }
        }

        // Set raw text for copy button (strip thinking tags)
        if (result && result.response) {
            streamMsg.setAttribute('data-raw-text', _stripThinkingTags(result.response));
        }

        // Replace content with full rendered markdown (sanitized to prevent XSS)
        var contentDiv = streamMsg.querySelector('.message-content');
        if (contentDiv && result && result.response) {
            var cleanResponse = _stripThinkingTags(result.response);
            contentDiv.innerHTML = DOMPurify.sanitize(marked.parse(cleanResponse));
        } else if (contentDiv && result && result.error) {
            // Show error in streaming message if no response text
            contentDiv.innerHTML = DOMPurify.sanitize('⚠️ ' + t('error') + ': ' + result.error);
        }

        // Display file attachments (image, video) from result if present
        if (result && (result.file_path || result.file_data)) {
            var fileUrl = '';
            if (result.file_path) {
                fileUrl = '/api/files/' + result.file_path;
            } else if (result.file_data) {
                fileUrl = 'data:' + (result.file_type || 'application/octet-stream') + ';base64,' + result.file_data;
            }
            if (fileUrl && result.file_type) {
                if (result.file_type.startsWith('image/')) {
                    var existingImg = streamMsg.querySelector('.image-container');
                    if (!existingImg) {
                        var imgContainer = document.createElement('div');
                        imgContainer.className = 'image-container';
                        var img = document.createElement('img');
                        img.src = fileUrl;
                        img.loading = 'lazy';
                        img.className = 'attached-image';
                        img.alt = result.file_name || t('image');
                        img.title = t('click_to_enlarge');
                        img.onclick = function() { openImageModal(this.src, result.file_name || t('image')); };
                        imgContainer.appendChild(img);
                        streamMsg.appendChild(imgContainer);
                    }
                } else if (result.file_type.startsWith('video/')) {
                    var existingVideo = streamMsg.querySelector('video');
                    if (!existingVideo) {
                        var video = document.createElement('video');
                        video.controls = true;
                        video.preload = 'metadata';
                        video.src = fileUrl;
                        video.style.maxWidth = '100%';
                        video.style.maxHeight = '400px';
                        streamMsg.appendChild(video);
                    }
                }
            }
        }

        if (typeof updateLastVisit === 'function') updateLastVisit(currentSessionId);
        if (typeof addCopyButtonsToMessage === 'function') addCopyButtonsToMessage(streamMsg);
    } else if (resultSessionId === currentSessionId && data.result?.response) {
        // Fallback: create the message via displayMessage
            window.displayMessage('assistant', data.result.response, null, null, null, null,
                data.result.assistant_timestamp || new Date().toISOString(),
                data.result.response_time, data.result.model_used,
                null, null, null, null, data.result.message_id,
                data.result.response_style, data.result.completion_tokens,
                data.result.file_size);
    }

    if (resultSessionId && resultSessionId !== currentSessionId) {
        setNewMessageIndicator(resultSessionId, true);
    }

    // Cleanup — but don't clear pending request if task was requeued
    if (resultSessionId) {
        setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
        setTimeout(fetchQueueStatus, 500);
    }
    var wasRequeued = data.result && data.result.status === 'queued' && data.result.request_id;
    if (!wasRequeued) {
        clearPendingRequest(taskId);
    }
    _clearStreamFromSessionStorage(taskId);
}

function trackPendingRequest(requestId, sessionId, persist = true) {
    pendingRequestIds[requestId] = { sessionId: sessionId, timestamp: Date.now() };
    if (!persist) return;
    // Persist to sessionStorage so status indicators survive session switches
    try {
        const stored = JSON.parse(sessionStorage.getItem('pendingRequests') || '{}');
        stored[requestId] = { sessionId: sessionId, timestamp: Date.now() };
        sessionStorage.setItem('pendingRequests', JSON.stringify(stored));
    } catch (e) { /* ignore */ }
}

function restorePendingRequests() {
    try {
        const stored = JSON.parse(sessionStorage.getItem('pendingRequests') || '{}');
        const now = Date.now();
        for (const [reqId, info] of Object.entries(stored)) {
            // Only restore if not too old (5 minutes)
            if (!pendingRequestIds[reqId] && now - info.timestamp < 5 * 60 * 1000) {
                pendingRequestIds[reqId] = info;
            }
        }
    } catch (e) { /* ignore */ }
}

function verifyPendingRequests() {
    // On SSE reconnect, check if any pending tasks already completed on the server.
    // Fixes stuck hourglasses when result_completed event was lost during disconnection.
    const ids = Object.keys(pendingRequestIds);
    if (ids.length === 0) return;

    ids.forEach(function (reqId) {
        fetchWithCSRF('/api/queue/result/' + reqId)
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.status === 'completed' && data.result) {
                    const info = pendingRequestIds[reqId];
                    const sessionId = info ? info.sessionId : null;
                    clearPendingRequest(reqId);
                    handleCompletedResult(data.result, sessionId);
                } else if (data.status === 'error') {
                    const info = pendingRequestIds[reqId];
                    const sessionId = info ? info.sessionId : null;
                    clearPendingRequest(reqId);
                    handleErrorResult(data, sessionId);
                }
                // 'pending' means still running — leave it in pendingRequestIds
            })
            .catch(function () { /* ignore network errors */ });
    });
}

function clearPendingRequest(requestId) {
    delete pendingRequestIds[requestId];
    try {
        const stored = JSON.parse(sessionStorage.getItem('pendingRequests') || '{}');
        delete stored[requestId];
        sessionStorage.setItem('pendingRequests', JSON.stringify(stored));
    } catch (e) { /* ignore */ }
}

function handleCompletedResult(result, expectedSessionId) {
    const resultSessionId = result.session_id || expectedSessionId;

    // Update sessionsData count to keep sidebar in sync
    if (resultSessionId && sessionsData[resultSessionId]) {
        sessionsData[resultSessionId].message_count = (sessionsData[resultSessionId].message_count || 0) + 1;
        sessionsData[resultSessionId].updated_at = new Date().toISOString();
        delete newMessageIndicators[resultSessionId];
    }

    // Transcription result — may spawn chained processing
    if (result.transcribed_text !== undefined && result.transcribed_text !== null) {
        handleTranscriptionResult(result, resultSessionId, expectedSessionId);
        return;
    }

    // Error result
    if (result.error) {
        if (resultSessionId === currentSessionId) {
            // Error headers intentionally get no ⏱️/🚀/🤖 — pass null for
            // responseTime and modelName='system'.
            window.displayMessage('assistant', '⚠️ ' + result.error, null, null, null, null,
                result.assistant_timestamp || new Date().toISOString(), null, 'system',
                null, null, null, null, result.message_id, null);
        }
        if (resultSessionId) setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
        setTimeout(fetchQueueStatus, 500);
        return;
    }

    // Multiple messages (camera tasks)
    if (result.messages) {
        handleCameraResult(result, resultSessionId);
        return;
    }

    // Re-queue case: task completed but created a new queue entry (e.g., video from text)
    if (result.status === 'queued' && result.request_id) {
        trackPendingRequest(result.request_id, resultSessionId);
        sessionQueueInfo[resultSessionId] = { processing: true, queued: 0, queue_position: 0, has_transcribing: false };
        if (typeof updateStatusCounter === 'function') updateStatusCounter();
        if (typeof fetchQueueStatus === 'function') fetchQueueStatus();
        return;
    }

    // Single response (text / image gen)
    if (result.response) {
        if (result.message_id && displayedMessageIds.has(result.message_id)) {
            dlog('Skipping duplicate response by ID', result.message_id);
        } else {
            let responseTime = result.response_time;
            let modelUsed = result.model_used;

            if (result.mm_time && result.gen_time) {
                responseTime = { mm_time: result.mm_time, gen_time: result.gen_time, mm_model: result.mm_model, gen_model: result.gen_model };
                modelUsed = result.gen_model;
            }

            if (resultSessionId === currentSessionId) {
                window.displayMessage('assistant', result.response, result.file_data,
                    result.file_type, result.file_name, result.file_path,
                    result.assistant_timestamp || new Date().toISOString(), responseTime, modelUsed,
                    null, null, null, null, result.message_id,
                    result.response_style, result.completion_tokens,
                    result.file_size);
                if (typeof updateLastVisit === 'function') updateLastVisit(currentSessionId);
            } else {
                setNewMessageIndicator(resultSessionId, true);
            }
        }
        if (resultSessionId) setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
        setTimeout(fetchQueueStatus, 500);
        return;
    }

    // No recognizable payload — clean up anyway
    if (resultSessionId) {
        setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
        setTimeout(fetchQueueStatus, 500);
    }
}

function handleTranscriptionResult(result, resultSessionId, expectedSessionId) {
    if (resultSessionId === currentSessionId) {
        if (!result.transcribed_message_id || !displayedMessageIds.has(result.transcribed_message_id)) {
            const transcribedText = result.transcribed_text || '(empty transcription)';
            var transcribedContent = JSON.stringify({prefix: '🎤 ' + t('transcribed') + ': ', text: transcribedText});
            window.displayMessage('assistant', transcribedContent, null, null, null, null,
                result.assistant_timestamp || new Date().toISOString(), result.response_time, 'whisper',
                null, null, null, null, result.transcribed_message_id, null);
            if (result.transcribed_message_id) displayedMessageIds.add(result.transcribed_message_id);
        }
        if (result.request_id) {
            trackPendingRequest(result.request_id, resultSessionId);
            sessionQueueInfo[resultSessionId] = { processing: true, queued: 0, queue_position: 0, has_transcribing: false };
            window.updateStatusCounter();
            if (typeof fetchQueueStatus === 'function') fetchQueueStatus();
        } else {
            // Audio file — no further processing, clear ⚡
            clearSessionQueue(resultSessionId);
            if (typeof fetchQueueStatus === 'function') fetchQueueStatus();

            // Also clear the safety‑valve cache to force UI redraw
            if (typeof updateSessionsListFromData === 'function') {
                window._lastSessionsJson = null;
                updateSessionsListFromData();
            }
        }
        setLocalTranscribing(resultSessionId, false);
    } else {
        if (result.request_id) {
            trackPendingRequest(result.request_id, resultSessionId);
        } else {
            setNewMessageIndicator(resultSessionId, true);
        }
        setLocalTranscribing(resultSessionId, false);
        window.updateStatusCounter();
    }
}

function handleCameraResult(result, resultSessionId) {
    const cameraSessionId = result.session_id || resultSessionId;
    if (cameraSessionId === currentSessionId) {
        for (const msg of result.messages) {
            if (msg.message_id && displayedMessageIds.has(msg.message_id)) continue;
            window.displayMessage('assistant', msg.response, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                msg.assistant_timestamp, msg.response_time, msg.model_used,
                null, null, null, null, msg.message_id, msg.response_style,
                msg.completion_tokens, msg.file_size);
        }
        if (typeof updateLastVisit === 'function') updateLastVisit(currentSessionId);
    } else if (cameraSessionId) {
        setNewMessageIndicator(cameraSessionId, true);
    }
    if (resultSessionId) setLocalTranscribing(resultSessionId, false);
    clearSessionQueue(resultSessionId);
    setTimeout(fetchQueueStatus, 500);
}

function handleErrorResult(data, expectedSessionId) {
    const errorSessionId = data.result?.session_id || expectedSessionId;
    if (errorSessionId === currentSessionId) {
        const errorMsg = data.result?.error || data.error || t('unknown_error');
        window.displayMessage('assistant', '⚠️ ' + t('error') + ': ' + errorMsg, null, null, null, null,
            new Date().toISOString(), null, 'system', null, null, null, null, null, null);
    }
    if (errorSessionId) {
        setLocalTranscribing(errorSessionId, false);
        clearSessionQueue(errorSessionId);
        setTimeout(fetchQueueStatus, 500);
    }
}

function clearSessionQueue(sessionId) {
    if (!sessionId) return;
    sessionQueueInfo[sessionId] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
    if (sessionsData[sessionId]) sessionsData[sessionId].queue_info = null;
    if (typeof updateUIFromQueueStatus === 'function') updateUIFromQueueStatus();
}

// ── message_new ──────────────────────────────────────────────────────

function onMessageNew(data) {
    if (!data || !data.session_id || !data.message_id) return;
    dlog('onMessageNew: session', data.session_id, 'msg', data.message_id, 'role', data.role);

    // Skip if message is for a different session or already displayed
    if (data.session_id !== currentSessionId) {
        setNewMessageIndicator(data.session_id, true);
        return;
    }
    if (displayedMessageIds.has(data.message_id)) {
        dlog('onMessageNew: already displayed', data.message_id);
        return;
    }
    if (document.querySelector('[data-message-id="' + data.message_id + '"]')) {
        displayedMessageIds.add(data.message_id);
        return;
    }

    // Use inline message data from SSE event (avoids fetching 50 messages)
    if (data.message) {
        var msg = data.message;
        if (!displayedMessageIds.has(msg.id)) {
            displayedMessageIds.add(msg.id);
            var responseTime = null;
            if (msg.response_time) {
                if (typeof msg.response_time === 'object') responseTime = msg.response_time;
                else if (!isNaN(parseFloat(msg.response_time))) responseTime = parseFloat(msg.response_time);
            }
            window.displayMessage(
                msg.role, msg.content, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                msg.timestamp, responseTime, msg.model_name,
                msg.mm_time, msg.gen_time, msg.mm_model, msg.gen_model, msg.id,
                msg.response_style, msg.completion_tokens
            );
            if (sessionsData[data.session_id]) {
                sessionsData[data.session_id].message_count = (sessionsData[data.session_id].message_count || 0) + 1;
            }
        }
        return;
    }

    // Fallback: fetch the full message data from server (legacy path)
    fetch('/api/sessions/' + data.session_id + '/messages?limit=50')
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (json) {
            if (!json || !json.messages) return;
            var messages = json.messages;
            // Find the specific message
            for (var i = 0; i < messages.length; i++) {
                var msg = messages[i];
                if (msg.id == data.message_id) {
                    if (displayedMessageIds.has(msg.id)) return;
                    displayedMessageIds.add(msg.id);
                    var responseTime = null;
                    if (msg.response_time) {
                        if (typeof msg.response_time === 'object') responseTime = msg.response_time;
                        else if (!isNaN(parseFloat(msg.response_time))) responseTime = parseFloat(msg.response_time);
                    }
                    window.displayMessage(
                        msg.role, msg.content, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                        msg.timestamp, responseTime, msg.model_name,
                        msg.mm_time, msg.gen_time, msg.mm_model, msg.gen_model, msg.id,
                        msg.response_style, msg.completion_tokens
                    );
                    if (sessionsData[data.session_id]) {
                        sessionsData[data.session_id].message_count = (sessionsData[data.session_id].message_count || 0) + 1;
                    }
                    return;
                }
            }
        })
        .catch(function () {});
}

// ── Progress restore after reconnect / page reload ──────────────────

async function restoreTaskProgress() {
    for (const [taskId, info] of Object.entries(pendingRequestIds)) {
        if (info.sessionId !== currentSessionId) continue;
        if (info._progressRestored) continue;

        try {
            const resp = await fetch('/api/queue/progress/' + taskId);
            const json = await resp.json();
            const progress = json && json.progress;
            if (!progress) continue;

            info._progressRestored = true;

            if (progress.type === 'video_step') {
                onVideoStep({
                    session_id: info.sessionId,
                    task_id: taskId,
                    step: progress.step,
                    total: progress.total,
                    percent: progress.percent,
                });
            } else if (progress.type === 'image_step') {
                onImageStep({
                    session_id: info.sessionId,
                    task_id: taskId,
                    step: progress.step,
                    total: progress.total,
                    percent: progress.percent,
                });
            } else if (progress.type === 'task_progress') {
                onTaskProgress({
                    session_id: info.sessionId,
                    task_id: taskId,
                    stage: progress.stage,
                });
            }
        } catch (e) {
            dlog('Failed to restore progress for', taskId, e);
        }
    }
}

// ── Initialisation ───────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {
    connectEventStream();
});

// Reconnect SSE when tab becomes visible again (mobile fix)
document.addEventListener('visibilitychange', function () {
    if (!document.hidden && (!eventSource || eventSource.readyState !== EventSource.OPEN)) {
        dlog('Tab visible, reconnecting SSE...');
        connectEventStream();
        if (typeof loadSessionsFromServer === 'function') loadSessionsFromServer();
        if (typeof fetchQueueStatus === 'function') fetchQueueStatus();
        if (typeof restoreTaskProgress === 'function') restoreTaskProgress();
    }
});

// Make functions globally accessible
window.connectEventStream = connectEventStream;
window.disconnectEventStream = disconnectEventStream;
window.trackPendingRequest = trackPendingRequest;
window.restoreStreamingFromSessionStorage = restoreStreamingFromSessionStorage;
window.restoreTaskProgress = restoreTaskProgress;

// Fallback polling: refresh queue status every 5s while there are pending requests
setInterval(function () {
    if (Object.keys(pendingRequestIds).length > 0 && typeof fetchQueueStatus === 'function') {
        fetchQueueStatus();
    }
}, 5000);
