// static/js/chat-queue.js
// Queue status functions – now with WebSocket support

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    // Sync interval for fallback polling when WebSocket is disconnected
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING) return;
        // Only use HTTP polling as fallback if WebSocket is not connected
        if (!socketConnected) {
            loadSessionsFromServer();
            fetchQueueStatus();
            window.updateStatusCounter();
        } else {
            // WebSocket is connected, minimal sync needed
            loadSessionsFromServer();
            window.updateStatusCounter();
        }
    }, 5000);
}

function fetchQueueStatus() {
    if (window.IS_RELOADING) return;
    // If WebSocket is connected, we don't need to poll status
    if (socketConnected) {
        return;
    }
    // Fallback to HTTP polling
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
    // Force additional redraws to ensure icon visibility
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
    
    // If WebSocket is connected, use data from sessionQueueInfo
    if (socketConnected) {
        const counter = document.getElementById('status-counter');
        if (counter) {
            // Count requests from sessionQueueInfo
            let userQueued = 0;
            let totalQueued = 0;
            
            for (const sessionId in sessionQueueInfo) {
                const info = sessionQueueInfo[sessionId];
                if (info.queued) userQueued += info.queued;
                if (info.queued) totalQueued += info.queued;
            }
            
            counter.textContent = '📊 ' + userQueued + '/' + totalQueued;
            counter.title = t('your_requests');
        }
        return;
    }
    
    // Fallback to HTTP polling
    fetch('/api/queue/counts')
    .then(response => response.json())
    .then(data => {
        if (window.IS_RELOADING) return;
        const counter = document.getElementById('status-counter');
        if (counter && data.user_queued !== undefined && data.total_queued !== undefined) {
            counter.textContent = '📊 ' + data.user_queued + '/' + data.total_queued;
            counter.title = t('your_requests');
        }
    })
    .catch(err => console.error('Error updating counter:', err));
};