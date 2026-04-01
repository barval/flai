// app/static/js/chat-queue.js
// Queue status functions

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    console.log('startSyncInterval: Starting sync interval (2 seconds) for session', currentSessionId);
    // Sync interval for queue status, counter updates, and cross-client synchronization
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING) {
            console.log('sync interval: Skipping - IS_RELOADING');
            return;
        }
        console.log('sync interval: Running sync for session', currentSessionId);
        // Always fetch queue status first to get latest data from server
        fetchQueueStatus();
        // Then sync sessions and messages
        syncSessionsAndMessages();
    }, 2000);
}

/**
 * Synchronize sessions and messages across multiple clients
 * Called periodically to keep all clients in sync
 */
function syncSessionsAndMessages() {
    if (window.IS_RELOADING) {
        console.log('syncSessionsAndMessages: Skipping - IS_RELOADING');
        return;
    }
    console.log('syncSessionsAndMessages: Starting sync for session', currentSessionId, 'pendingRequests:', Object.keys(pendingRequests).length);

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
        console.log('syncSessionsAndMessages: Calling syncMessagesForCurrentSession');
        syncMessagesForCurrentSession();
    } else {
        console.log('syncSessionsAndMessages: No current session, skipping message sync');
    }
}

/**
 * Sync messages for current session from server
 * Checks for new messages from other clients
 */
function syncMessagesForCurrentSession() {
    if (window.IS_RELOADING || !currentSessionId) {
        console.log('syncMessages: Skipping - IS_RELOADING or no currentSessionId');
        return;
    }

    // Get last message timestamp from DOM
    const messagesContainer = document.getElementById('chat-messages');
    const lastMessageEl = messagesContainer.lastElementChild;
    
    // If no messages in DOM, load all messages (not just new ones)
    if (!lastMessageEl || !lastMessageEl.dataset.timestamp) {
        console.log('syncMessages: No messages in DOM, loading all messages for session', currentSessionId);
        loadMessages(currentSessionId).catch(err => {
            console.error('syncMessages: Error loading all messages:', err);
        });
        return;
    }

    const lastTimestamp = lastMessageEl.dataset.timestamp;
    console.log('syncMessages: Last message timestamp from DOM:', lastTimestamp);
    console.log('syncMessages: Fetching messages from /api/sessions/', currentSessionId, '/messages?since=', encodeURIComponent(lastTimestamp));

    fetch(`/api/sessions/${currentSessionId}/messages?since=${encodeURIComponent(lastTimestamp)}`)
        .then(res => {
            console.log('syncMessages: Response status:', res.status);
            return res.json();
        })
        .then(data => {
            // Handle both old format (array) and new format (object with messages)
            const newMessages = Array.isArray(data) ? data : (data.messages || []);
            console.log('syncMessages: Received', newMessages.length, 'new messages');
            console.log('syncMessages: Messages:', newMessages.map(m => ({ id: m.id, role: m.role, timestamp: m.timestamp })));
            if (window.IS_RELOADING || !newMessages || newMessages.length === 0) {
                console.log('syncMessages: No new messages to display');
                return;
            }

            let displayedCount = 0;

            // Display new messages
            for (const msg of newMessages) {
                console.log('syncMessages: Processing message:', msg.id, msg.role);

                // Clear unread indicator when we receive new messages for current session
                delete newMessageIndicators[currentSessionId];

                // Skip if already displayed (check DOM first)
                if (msg.id) {
                    const existingMsg = document.querySelector(`[data-message-id="${msg.id}"]`);
                    if (existingMsg) {
                        console.log('syncMessages: Message', msg.id, 'already in DOM, skipping');
                        displayedMessageIds.add(msg.id);
                        continue;
                    }
                }

                // Skip if already in displayedMessageIds Set
                if (msg.id && displayedMessageIds.has(msg.id)) {
                    console.log('syncMessages: Message', msg.id, 'already in displayedMessageIds, skipping');
                    continue;
                }

                // Display message
                displayedCount++;
                console.log('syncMessages: Displaying message:', msg.id, msg.role);

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

            console.log('syncMessages: Displayed', displayedCount, 'messages');
        })
        .catch(err => console.error('Error syncing messages:', err));
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
                if (proc.type === 'transcribe_audio' || proc.type === 'audio') {
                    newInfo[sessionId].has_transcribing = true;
                }
                console.log('fetchQueueStatus: processing task for session', sessionId, 'type:', proc.type);
            }

            // Process queued tasks
            data.queued.forEach(item => {
                const sessionId = item.session_id;
                const position = item.position_info?.position || 999;
                if (!newInfo[sessionId]) {
                    newInfo[sessionId] = { processing: false, queued: 0, queue_position: 999, has_transcribing: false };
                }
                newInfo[sessionId].queued += 1;
                // Store the position for this session's task
                newInfo[sessionId].queue_position = position;
            });

            // Update global sessionQueueInfo
            sessionQueueInfo = newInfo;
            console.log('fetchQueueStatus: sessionQueueInfo updated', sessionQueueInfo);
            
            // Update sessions list with new queue info
            updateSessionsListFromData();
            
            // Update status counter
            window.updateStatusCounter();
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
                console.log('updateStatusCounter:', data.user_queued + '/' + data.total_queued);
            }
        })
        .catch(err => console.error('Error updating counter:', err));
};