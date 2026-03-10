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
        const now = Date.now();
        const agg = {};
        if (data.processing) {
            const proc = data.processing;
            if (!agg[proc.session_id]) agg[proc.session_id] = { processing: false, queued: 0 };
            agg[proc.session_id].processing = true;
        }
        data.queued.forEach(item => {
            if (!agg[item.session_id]) agg[item.session_id] = { processing: false, queued: 0 };
            agg[item.session_id].queued += 1;
        });
        const newStable = {};
        Object.keys(agg).forEach(sid => {
            const current = agg[sid];
            const prev = stableSessionStatus[sid];
            if (!prev || prev.processing !== current.processing || prev.queued !== current.queued) {
                if (!prev || prev.pendingChange) {
                    if (prev && now - prev.changeTime > 6000) {
                        newStable[sid] = {
                            processing: current.processing,
                            queued: current.queued,
                            changeTime: now,
                            pendingChange: false
                        };
                    } else {
                        newStable[sid] = {
                            ...prev,
                            pendingChange: true,
                            changeTime: prev ? prev.changeTime : now
                        };
                    }
                } else {
                    newStable[sid] = {
                        ...current,
                        changeTime: now,
                        pendingChange: true
                    };
                }
            } else {
                newStable[sid] = { ...current, changeTime: now, pendingChange: false };
            }
        });
        Object.keys(stableSessionStatus).forEach(sid => {
            if (!agg[sid] && (now - stableSessionStatus[sid].changeTime) > 10000) {
                delete stableSessionStatus[sid];
            }
        });
        stableSessionStatus = newStable;
        sessionQueueInfo = {};
        Object.keys(stableSessionStatus).forEach(sid => {
            sessionQueueInfo[sid] = {
                processing: stableSessionStatus[sid].processing,
                queued: stableSessionStatus[sid].queued
            };
        });
        updateSessionsListFromData();
    })
    .catch(err => console.error('Error fetching queue status:', err));
}

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