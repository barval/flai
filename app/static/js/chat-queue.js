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

// Local transcribing status (for voice messages)
function setLocalTranscribing(sessionId, isTranscribing) {
    if (!sessionId) return;
    if (isTranscribing) {
        localTranscribingSessions[sessionId] = true;
        // Immediate update to show mic
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
    } else {
        // Use setTimeout to allow mic to be visible briefly before removal
        setTimeout(() => {
            delete localTranscribingSessions[sessionId];
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
        }, 50); // small delay to ensure mic was shown
    }
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