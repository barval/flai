// app/static/js/chat-queue.js
// Queue status functions

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    console.debug('startSyncInterval: Starting sync interval (2000ms) for session', currentSessionId);
    // Sync interval for queue status, counter updates, and cross-client synchronization
    // Using 2000ms to allow CSS animations to run smoothly between updates
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING) {
            console.debug('sync interval: Skipping - IS_RELOADING');
            return;
        }
        console.debug('sync interval: Running sync for session', currentSessionId);
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
        console.debug('syncSessionsAndMessages: Skipping - IS_RELOADING');
        return;
    }
    console.debug('syncSessionsAndMessages: Starting sync for session', currentSessionId, 'pendingRequests:', Object.keys(pendingRequests).length);

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
        console.debug('syncSessionsAndMessages: Calling syncMessagesForCurrentSession');
        syncMessagesForCurrentSession();
    } else {
        console.debug('syncSessionsAndMessages: No current session, skipping message sync');
    }
}

/**
 * Sync messages for current session from server
 * Checks for new messages from other clients
 */
function syncMessagesForCurrentSession() {
    if (window.IS_RELOADING || !currentSessionId) {
        console.debug('syncMessages: Skipping - IS_RELOADING or no currentSessionId');
        return;
    }

    // Get last message timestamp from DOM
    const messagesContainer = document.getElementById('chat-messages');
    const lastMessageEl = messagesContainer.lastElementChild;
    
    // If no messages in DOM, load all messages (not just new ones)
    if (!lastMessageEl || !lastMessageEl.dataset.timestamp) {
        console.debug('syncMessages: No messages in DOM, loading all messages for session', currentSessionId);
        loadMessages(currentSessionId).catch(err => {
            console.error('syncMessages: Error loading all messages:', err);
        });
        return;
    }

    const lastTimestamp = lastMessageEl.dataset.timestamp;
    console.debug('syncMessages: Last message timestamp from DOM:', lastTimestamp);
    console.debug('syncMessages: Fetching messages from /api/sessions/', currentSessionId, '/messages?since=', encodeURIComponent(lastTimestamp));

    fetch(`/api/sessions/${currentSessionId}/messages?since=${encodeURIComponent(lastTimestamp)}`)
        .then(res => {
            console.debug('syncMessages: Response status:', res.status);
            return res.json();
        })
        .then(data => {
            // Handle both old format (array) and new format (object with messages)
            const newMessages = Array.isArray(data) ? data : (data.messages || []);
            console.debug('syncMessages: Received', newMessages.length, 'new messages');
            console.debug('syncMessages: Messages:', newMessages.map(m => ({ id: m.id, role: m.role, timestamp: m.timestamp })));
            if (window.IS_RELOADING || !newMessages || newMessages.length === 0) {
                console.debug('syncMessages: No new messages to display');
                return;
            }

            let displayedCount = 0;

            // Display new messages
            for (const msg of newMessages) {
                console.debug('syncMessages: Processing message:', msg.id, msg.role);

                // Clear unread indicator when we receive new messages for current session
                delete newMessageIndicators[currentSessionId];

                // Skip if already displayed (check DOM first)
                if (msg.id) {
                    const existingMsg = document.querySelector(`[data-message-id="${msg.id}"]`);
                    if (existingMsg) {
                        console.debug('syncMessages: Message', msg.id, 'already in DOM, skipping');
                        displayedMessageIds.add(msg.id);
                        continue;
                    }
                }

                // Skip if already in displayedMessageIds Set
                if (msg.id && displayedMessageIds.has(msg.id)) {
                    console.debug('syncMessages: Message', msg.id, 'already in displayedMessageIds, skipping');
                    continue;
                }

                // Display message
                displayedCount++;
                console.debug('syncMessages: Displaying message:', msg.id, msg.role);

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

            console.debug('syncMessages: Displayed', displayedCount, 'messages');
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

            // Initialize all known sessions with default values
            // IMPORTANT: Don't reset processing=false for sessions with pending requests
            // They might be between server-side processing state transitions
            Object.keys(sessionsData).forEach(sessionId => {
                const hasPendingRequest = Object.values(pendingRequests).some(
                    pr => pr.sessionId === sessionId
                );
                const existingInfo = sessionQueueInfo[sessionId] || {};

                // If this session has a pending request, preserve its processing state
                // unless the server explicitly says it's not processing
                newInfo[sessionId] = {
                    processing: hasPendingRequest ? (existingInfo.processing || false) : false,
                    queued: hasPendingRequest ? (existingInfo.queued || 0) : 0,
                    queue_position: hasPendingRequest ? (existingInfo.queue_position || 0) : 0,
                    // has_transcribing should ONLY persist if the server confirms it
                    // Don't carry it over from existing state — it will be set below if needed
                    has_transcribing: false
                };
            });

            // Process currently processing task (ONLY ONE session can have this)
            let processingSessionId = null;
            if (data.processing) {
                const proc = data.processing;
                processingSessionId = proc.session_id;
                // Ensure the session exists in newInfo before setting properties
                if (!newInfo[processingSessionId]) {
                    newInfo[processingSessionId] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                }
                newInfo[processingSessionId].processing = true;
                newInfo[processingSessionId].queued = 0;
                newInfo[processingSessionId].queue_position = 0;
                // Only set has_transcribing if currently processing audio/transcribe task
                if (proc.type === 'transcribe_audio' || proc.type === 'audio') {
                    newInfo[processingSessionId].has_transcribing = true;
                }
                console.debug('fetchQueueStatus: processing task for session', processingSessionId, 'type:', proc.type);
            }

            // Process queued tasks (EXCLUDE the session that's currently processing)
            data.queued.forEach(item => {
                const sessionId = item.session_id;
                // Skip if this session is already processing
                if (sessionId === processingSessionId) {
                    return;
                }
                const position = item.position_info?.position || 999;
                if (!newInfo[sessionId]) {
                    newInfo[sessionId] = { processing: false, queued: 0, queue_position: 999, has_transcribing: false };
                }
                newInfo[sessionId].queued += 1;
                // Store the position for this session's task
                newInfo[sessionId].queue_position = position;
                // If server says this is queued (not processing), clear the processing flag
                // This overrides the "preserve for pending" logic above
                newInfo[sessionId].processing = false;
            });

            // Clear processing flag for sessions that were processing but are no longer
            // (server has moved on to a different session or completed)
            Object.keys(newInfo).forEach(sessionId => {
                // If this session is not the current processing session and has no queued tasks,
                // and it was previously marked as processing - clear it
                if (sessionId !== processingSessionId &&
                    newInfo[sessionId].queued === 0 &&
                    newInfo[sessionId].processing) {
                    // Check if there's still a pending request for this session
                    const hasPending = Object.values(pendingRequests).some(
                        pr => pr.sessionId === sessionId
                    );
                    if (!hasPending) {
                        newInfo[sessionId].processing = false;
                    }
                    // If there IS a pending request, keep processing=true - the server
                    // may have just cleared the processing key before we polled
                }
            });

            // Update global sessionQueueInfo
            sessionQueueInfo = newInfo;
            console.debug('fetchQueueStatus: sessionQueueInfo updated', sessionQueueInfo);

            // Sync localTranscribingSessions with server status
            // This ensures mobile clients show microphone icon for transcribing sessions
            Object.keys(newInfo).forEach(sessionId => {
                if (newInfo[sessionId].has_transcribing) {
                    localTranscribingSessions[sessionId] = true;
                } else if (localTranscribingSessions[sessionId]) {
                    // Server says no longer transcribing, clear local flag
                    delete localTranscribingSessions[sessionId];
                }
            });

            // CRITICAL: Also update sessionsData with queue_info for immediate display
            Object.keys(newInfo).forEach(sessionId => {
                if (sessionsData[sessionId]) {
                    sessionsData[sessionId].queue_info = { ...newInfo[sessionId] };
                }
            });

            // CRITICAL: Update sessions list IMMEDIATELY (no debounce)
            // This ensures status icons update immediately after task completion
            const sessions = Object.keys(sessionsData).map(id => ({
                id: id,
                title: sessionsData[id].title,
                updated_at: sessionsData[id].updated_at,
                message_count: sessionsData[id].message_count,
                has_unread: (sessionsData[id].has_unread || newMessageIndicators[id]) ? true : false,
                queue_info: sessionQueueInfo[id] || null
            }));
            
            if (typeof updateSessionsList === 'function') {
                updateSessionsList(sessions);
            }

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

    console.debug('setLocalTranscribing called:', sessionId, isTranscribing);

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

    // Force immediate update with full session data
    const sessions = Object.keys(sessionsData).map(id => ({
        id: id,
        title: sessionsData[id].title,
        updated_at: sessionsData[id].updated_at,
        message_count: sessionsData[id].message_count,
        has_unread: (sessionsData[id].has_unread || newMessageIndicators[id]) ? true : false,
        queue_info: sessionQueueInfo[id] || null
    }));
    updateSessionsList(sessions);

    console.debug('Transcribing flag', isTranscribing ? 'SET' : 'CLEARED', 'for session:', sessionId);
}

// Make function globally accessible
window.setLocalTranscribing = setLocalTranscribing;

window.updateStatusCounter = function() {
    if (window.IS_RELOADING) return;
    fetch('/api/queue/counts')
        .then(response => {
            // Check if response is JSON
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                console.error('Queue counter returned non-JSON response:', response.status);
                return null;
            }
            return response.json();
        })
        .then(data => {
            if (window.IS_RELOADING || !data) return;
            const counter = document.getElementById('status-counter');
            if (counter) {
                counter.textContent = '📊 ' + data.user_queued + '/' + data.total_queued;
                counter.title = t('your_requests');
                console.debug('updateStatusCounter:', data.user_queued + '/' + data.total_queued);
            }
        })
        .catch(err => console.error('Error updating counter:', err));
};

// Force sync when tab becomes visible again (fixes mobile "stuck" status)
document.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        console.debug('Tab became visible, forcing immediate sync');
        fetchQueueStatus();
        syncSessionsAndMessages();
    }
});