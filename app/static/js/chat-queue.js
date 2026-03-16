// static/js/chat-queue.js
// Queue status functions

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING) return;
        loadSessionsFromServer();
        fetchQueueStatus();
        window.updateStatusCounter();
    }, 5000);
}

function fetchQueueStatus() {
    if (window.IS_RELOADING) return;
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

// Local processing status (used for synchronous operations like transcription)
function setLocalProcessing(sessionId, isProcessing) {
    if (!sessionId) return;
    if (isProcessing) {
        localProcessingSessions[sessionId] = true;
    } else {
        delete localProcessingSessions[sessionId];
    }
    // Immediately update sessions list, bypassing debounce
    if (sessionsUpdateTimeout) {
        clearTimeout(sessionsUpdateTimeout);
        sessionsUpdateTimeout = null;
    }
    const sessions = Object.keys(sessionsData).map(id => ({
        id: id,
        title: sessionsData[id].title,
        updated_at: sessionsData[id].updated_at,
        message_count: sessionsData[id].message_count
    }));
    updateSessionsList(sessions);
}

// Make function globally accessible
window.setLocalProcessing = setLocalProcessing;

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