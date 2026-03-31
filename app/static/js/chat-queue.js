// app/static/js/chat-queue.js
// Queue status functions

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    // Sync interval for queue status, counter updates, and cross-client synchronization
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING) return;
        fetchQueueStatus();
        window.updateStatusCounter();
        syncSessionsAndMessages();
    }, 3000);
}

/**
 * Synchronize sessions and messages across multiple clients
 * Called periodically to keep all clients in sync
 */
function syncSessionsAndMessages() {
    if (window.IS_RELOADING) return;
    
    // Sync sessions list
    loadSessionsFromServer().then(sessions => {
        if (window.IS_RELOADING) return;
        
        // Check if current session still exists
        if (currentSessionId && !sessions.find(s => s.id === currentSessionId)) {
            console.warn('Current session no longer exists, redirecting to first session');
            if (sessions.length > 0) {
                switchSession(sessions[0].id);
            }
        }
    }).catch(err => console.error('Error syncing sessions:', err));
    
    // Sync messages for current session
    if (currentSessionId) {
        syncMessagesForCurrentSession();
    }
}

/**
 * Sync messages for current session from server
 * Checks for new messages from other clients
 * 
 * NOTE: This function has the same duplicate protection as pollNewMessages
 * to prevent displaying messages multiple times across different sync mechanisms
 */
function syncMessagesForCurrentSession() {
    if (window.IS_RELOADING || !currentSessionId) return;

    // Don't sync if there are active pending requests (own messages)
    const hasActiveRequests = Object.keys(pendingRequests).length > 0;
    if (hasActiveRequests) {
        return;
    }

    // Get last message timestamp from DOM
    const messagesContainer = document.getElementById('chat-messages');
    const lastMessageEl = messagesContainer.lastElementChild;

    if (!lastMessageEl || !lastMessageEl.dataset.timestamp) {
        return;
    }

    const lastTimestamp = lastMessageEl.dataset.timestamp;

    fetch(`/api/sessions/${currentSessionId}/messages?since=${encodeURIComponent(lastTimestamp)}`)
        .then(res => res.json())
        .then(newMessages => {
            if (window.IS_RELOADING || !newMessages || newMessages.length === 0) return;

            let hasNewMessagesFromOtherClient = false;

            // Display new messages from other clients
            for (const msg of newMessages) {
                // FIX 1: Skip if already displayed (check DOM first)
                if (msg.id) {
                    const existingMsg = document.querySelector(`[data-message-id="${msg.id}"]`);
                    if (existingMsg) {
                        console.log('syncMessages: Message', msg.id, 'already in DOM, skipping');
                        displayedMessageIds.add(msg.id);
                        continue;
                    }
                }

                // FIX 2: Skip if already displayed (check Set)
                if (msg.id && displayedMessageIds.has(msg.id)) {
                    console.log('syncMessages: Message', msg.id, 'already in displayedMessageIds, skipping');
                    continue;
                }

                // FIX 3: Skip user's own messages (they are displayed immediately)
                if (msg.role === 'user') {
                    if (msg.id) displayedMessageIds.add(msg.id);
                    continue;
                }

                // This is a new message from another client
                hasNewMessagesFromOtherClient = true;

                // Display assistant message from other client
                console.log('syncMessages: New message from other client:', msg.id);

                let responseTime = null;
                if (msg.response_time) {
                    if (typeof msg.response_time === 'object') {
                        responseTime = msg.response_time;
                    } else if (!isNaN(parseFloat(msg.response_time))) {
                        responseTime = parseFloat(msg.response_time);
                    }
                }

                window.displayMessage(
                    msg.role,
                    msg.content,
                    msg.file_data,
                    msg.file_type,
                    msg.file_name,
                    msg.file_path,
                    msg.timestamp,
                    responseTime,
                    msg.model_name,
                    msg.mm_time,
                    msg.gen_time,
                    msg.mm_model,
                    msg.gen_model,
                    msg.id
                );
            }

            // Show notification if new messages from other client were received
            if (hasNewMessagesFromOtherClient) {
                console.log('syncMessages: New messages detected from other client');
                // Update unread indicator for current session (it's already active, so just notify)
                showCrossClientNotification();
            }
        })
        .catch(err => console.error('Error syncing messages:', err));
}

/**
 * Show a brief notification about new messages from other clients
 */
function showCrossClientNotification() {
    // Show a subtle visual indicator
    const chatContainer = document.getElementById('chat-messages');
    if (!chatContainer) return;
    
    // Create notification element
    const notification = document.createElement('div');
    notification.className = 'cross-client-notification';
    notification.textContent = '📢 Новые сообщения от другого клиента';
    notification.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: #2ecc71;
        color: white;
        padding: 12px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        z-index: 10000;
        animation: slideIn 0.3s ease-out;
    `;
    
    document.body.appendChild(notification);
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

function fetchQueueStatus() {
    if (window.IS_RELOADING) return;
    fetch('/api/queue/status')
        .then(res => res.json())
        .then(data => {
            if (window.IS_RELOADING) return;
            const newInfo = {};
            
            // Process currently processing task
            if (data.processing) {
                const proc = data.processing;
                const sessionId = proc.session_id;
                if (!newInfo[sessionId]) {
                    newInfo[sessionId] = { processing: false, queued: 0, has_transcribing: false };
                }
                newInfo[sessionId].processing = true;
                // Only set has_transcribing if currently processing audio/transcribe task
                // This should only be true during actual transcription, not during text processing
                if (proc.type === 'transcribe_audio' || proc.type === 'audio') {
                    newInfo[sessionId].has_transcribing = true;
                }
            }
            
            // Process queued tasks - count them but don't set has_transcribing for queued tasks
            // has_transcribing should only be true when actively transcribing (processing)
            data.queued.forEach(item => {
                const sessionId = item.session_id;
                if (!newInfo[sessionId]) {
                    newInfo[sessionId] = { processing: false, queued: 0, has_transcribing: false };
                }
                newInfo[sessionId].queued += 1;
                // Note: We don't set has_transcribing for queued tasks
                // This ensures microphone icon only shows during active transcription
            });
            
            sessionQueueInfo = newInfo;
            updateSessionsListFromData();
        })
        .catch(err => console.error('Error fetching queue status:', err));
}

// Local transcribing status (for voice messages)
function setLocalTranscribing(sessionId, isTranscribing) {
    if (!sessionId) {
        console.warn('setLocalTranscribing called with empty sessionId');
        return;
    }
    
    console.log('setLocalTranscribing called:', sessionId, isTranscribing);
    
    if (isTranscribing) {
        localTranscribingSessions[sessionId] = true;
    } else {
        delete localTranscribingSessions[sessionId];
    }
    
    // Immediate update - clear any pending timeout
    if (sessionsUpdateTimeout) {
        clearTimeout(sessionsUpdateTimeout);
        sessionsUpdateTimeout = null;
    }
    
    // Force immediate update with current data
    const sessions = Object.keys(sessionsData).map(id => ({
        id: id,
        title: sessionsData[id].title,
        updated_at: sessionsData[id].updated_at,
        message_count: sessionsData[id].message_count
    }));
    updateSessionsList(sessions);
    
    // FIX: Force additional redraws for all devices (not just mobile) to ensure icon visibility
    if (isTranscribing) {
        setTimeout(() => updateSessionsList(sessions), 200);
        setTimeout(() => updateSessionsList(sessions), 400);
    }
    
    console.log('Transcribing flag', isTranscribing ? 'SET' : 'CLEARED', 'for session:', sessionId);
}

// Make function globally accessible
window.setLocalTranscribing = setLocalTranscribing;

window.updateStatusCounter = function() {
    if (window.IS_RELOADING) return;
    fetch('/api/queue/counts')
        .then(response => response.json())
        .then(data => {
            if (window.IS_RELOADING) return;
            const counter = document.getElementById('status-counter');
            if (counter) {
                counter.textContent = '📊 ' + data.user_queued + '/' + data.total_queued;
                counter.title = t('your_requests');
            }
        })
        .catch(err => console.error('Error updating counter:', err));
};