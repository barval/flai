// app/static/js/chat-queue.js
// Queue status functions (no longer uses polling — relies on SSE events)

function fetchQueueStatus() {
    if (window.IS_RELOADING) return;
    fetch('/api/queue/status')
        .then(res => {
            if (!res.ok) {
                // Server unavailable — clear all status icons
                Object.keys(sessionQueueInfo).forEach(sid => {
                    sessionQueueInfo[sid] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                });
                updateUIFromQueueStatus();
                return;
            }
            return res.json();
        })
        .then(data => {
            if (window.IS_RELOADING) return;
            if (!data) return;

            // COMPLETELY rebuild sessionQueueInfo from server data only
            // Do NOT use pendingRequests to determine status icons
            const newInfo = {};

            // Start with all known sessions as idle
            Object.keys(sessionsData).forEach(sessionId => {
                newInfo[sessionId] = {
                    processing: false,
                    queued: 0,
                    queue_position: 0,
                    has_transcribing: false
                };
            });

            // Mark the session that is currently being processed by server (lightning bolt)
            let processingSessionId = null;
            if (data.processing) {
                processingSessionId = data.processing.session_id;
                if (!newInfo[processingSessionId]) {
                    newInfo[processingSessionId] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                }
                // Show processing ⚡ for the currently processing session.
                // Server already filters by user_id, so if data.processing is
                // set, the session is genuinely being processed.  We removed the
                // pendingRequestIds guard so that ⚡ also appears after page
                // refresh even when sessionStorage restore didn't run yet.
                newInfo[processingSessionId].processing = true;
                if (data.processing.type === 'transcribe_audio' || data.processing.type === 'audio') {
                    newInfo[processingSessionId].has_transcribing = true;
                }
            }

            // Mark queued sessions (hourglass)
            if (data.queued && data.queued.length > 0) {
                data.queued.forEach(item => {
                    const sessionId = item.session_id;
                    if (sessionId === processingSessionId) return;
                    if (!newInfo[sessionId]) {
                        newInfo[sessionId] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                    }
                    newInfo[sessionId].queued += 1;
                    newInfo[sessionId].queue_position = item.position_info?.position ?? 0;
                });
            }

            // Preserve hourglass for sessions with pending requests that server hasn't
            // reported yet (race between client set hourglass and server add to queue).
            // Without this, fetchQueueStatus() overwrites local ⏳ with empty server data
            // and the hourglass disappears until the next poll or SSE event.
            // queue_position is intentionally left at 0 here (no real position known)
            // so the UI shows ⏳ without a number instead of a duplicate "999" across
            // multiple pending sessions.
            //
            // TTL: stale entries (older than MAX_PENDING_AGE_MS) are dropped on every
            // fetchQueueStatus() call. This prevents "phantom" hourglasses caused by
            // missed SSE result_completed events — the server-side count (4/4) is the
            // source of truth, so we should not keep showing ⏳ for tasks the server
            // has long since forgotten.
            const MAX_PENDING_AGE_MS = 5 * 60 * 1000;
            const now = Date.now();
            if (typeof pendingRequestIds === 'object' && pendingRequestIds) {
                let storedMap = null;
                try {
                    storedMap = JSON.parse(sessionStorage.getItem('pendingRequests') || '{}');
                } catch (e) { /* ignore */ }
                Object.keys(pendingRequestIds).forEach(reqId => {
                    const info = pendingRequestIds[reqId];
                    if (!info || !info.sessionId) return;
                    if (info.timestamp && now - info.timestamp > MAX_PENDING_AGE_MS) {
                        delete pendingRequestIds[reqId];
                        if (storedMap && Object.prototype.hasOwnProperty.call(storedMap, reqId)) {
                            delete storedMap[reqId];
                        }
                        return;
                    }
                    const sid = info.sessionId;
                    if (sid === processingSessionId) return;
                    if (!newInfo[sid]) {
                        newInfo[sid] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                    }
                    if (!newInfo[sid].processing && newInfo[sid].queued === 0) {
                        newInfo[sid].queued = 1;
                    }
                });
                if (storedMap) {
                    try {
                        sessionStorage.setItem('pendingRequests', JSON.stringify(storedMap));
                    } catch (e) { /* ignore */ }
                }
            }

            // SAFETY VALVE: If server reports idle (no processing, no queued), clear stale pendingRequestIds
            // Fixes stuck hourglasses when SSE missed the result_completed event
            if (!processingSessionId && (!data.queued || data.queued.length === 0)) {
                Object.keys(pendingRequestIds).forEach(reqId => {
                    delete pendingRequestIds[reqId];
                });
                try { sessionStorage.removeItem('pendingRequests'); } catch (e) { /* ignore */ }
            }

            // Update global sessionQueueInfo
            sessionQueueInfo = newInfo;

            // Sync localTranscribingSessions with server status
            Object.keys(newInfo).forEach(sessionId => {
                if (newInfo[sessionId].has_transcribing) {
                    localTranscribingSessions[sessionId] = true;
                } else if (localTranscribingSessions[sessionId]) {
                    delete localTranscribingSessions[sessionId];
                }
            });

            // Update sessionsData with queue_info for display
            Object.keys(newInfo).forEach(sessionId => {
                if (sessionsData[sessionId]) {
                    sessionsData[sessionId].queue_info = { ...newInfo[sessionId] };
                }
            });

            updateUIFromQueueStatus();
            // Ensure pending requests are restored after queue status update
            restorePendingRequests();
        })
        .catch(err => console.error('Error fetching queue status:', err));
}

/**
 * Update the sessions list UI based on current sessionQueueInfo
 * Called after fetchQueueStatus or status changes.
 * Uses JSON-based deduplication to avoid full DOM rebuild when
 * nothing meaningful changed (prevents flickering on slow/mobile connections).
 */
function updateUIFromQueueStatus() {
    const sessions = Object.keys(sessionsData).map(id => ({
        id: id,
        title: sessionsData[id].title,
        updated_at: sessionsData[id].updated_at,
        message_count: sessionsData[id].message_count,
        has_unread: (sessionsData[id].has_unread || newMessageIndicators[id]) ? true : false,
        queue_info: sessionQueueInfo[id] || null
    }));

    // Skip full DOM rebuild if sessions data hasn't changed
    const sessionsJson = JSON.stringify(sessions);
    if (window._lastSessionsJson === sessionsJson) {
        // Only update the status counter — sessions list is unchanged
        window.updateStatusCounter();
        return;
    }
    window._lastSessionsJson = sessionsJson;

    if (typeof updateSessionsList === 'function') {
        updateSessionsList(sessions);
    }

    // Update status counter
    window.updateStatusCounter();
}

function setLocalTranscribing(sessionId, isTranscribing) {
    if (!sessionId) {
        dwarn('setLocalTranscribing called with empty sessionId');
        return;
    }

    dlog('setLocalTranscribing called:', sessionId, isTranscribing);

    if (isTranscribing) {
        localTranscribingSessions[sessionId] = true;
    } else {
        delete localTranscribingSessions[sessionId];
    }

    // Use the standard debounced update instead of immediate full rebuild
    if (typeof updateUIFromQueueStatus === 'function') {
        updateUIFromQueueStatus();
    }

    dlog('Transcribing flag', isTranscribing ? 'SET' : 'CLEARED', 'for session:', sessionId);
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
                dlog('updateStatusCounter:', data.user_queued + '/' + data.total_queued);
            }
        })
        .catch(err => console.error('Error updating counter:', err));
};

// visibilitychange handled by events.js (SSE reconnect)