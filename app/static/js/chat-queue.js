// app/static/js/chat-queue.js
// Queue status functions

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    // Sync interval for queue status and counter updates
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING) return;
        fetchQueueStatus();
        window.updateStatusCounter();
    }, 2000);
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