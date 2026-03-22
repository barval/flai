// static/js/chat-queue.js
// Queue status functions – now with WebSocket support

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    // FIX: Reduced interval from 5000ms to 1000ms for more responsive status updates
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING) return;
        // Only use HTTP polling as fallback if WebSocket is not connected
        if (!socketConnected) {
            loadSessionsFromServer();
            fetchQueueStatus();
            window.updateStatusCounter();
        } else {
            // Still fetch sessions to keep in sync, but queue status is handled via WebSocket
            loadSessionsFromServer();
            window.updateStatusCounter(); // This will still use HTTP counts, but we can also remove it
        }
    }, 5000); // Keep longer interval as fallback
}

function fetchQueueStatus() {
    if (window.IS_RELOADING) return;
    // If WebSocket is connected, we don't need to poll status; but we can keep for fallback
    if (socketConnected) {
        // Still update sessions list periodically (already done)
        return;
    }
    // Fallback to HTTP
    fetch('/api/queue/status')
    .then(res => res.json())
    .then(data => {
        if (window.IS_RELOADING) return;
        const newInfo = {};
        if (data.processing) {
            const proc = data.processing;
            newInfo[proc.session_id] = { processing: true, queued: 0 };
        }
        data.queued.forEach(item => {
            if (!newInfo[item.session_id]) {
                newInfo[item.session_id] = { processing: false, queued: 0 };
            }
            newInfo[item.session_id].queued += 1;
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