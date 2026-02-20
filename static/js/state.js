// Глобальное состояние приложения

export const appState = {
    currentSessionId: null,
    sessions: [],
    messagesCache: {},
    newMessageIndicators: {},
    processedRequests: new Set(),
    requestProcessingTimes: new Map(),
    defaultModelName: 'qwen3-vl:8b-instruct',
    displayedMessages: new Set(), // для отслеживания уже отображённых сообщений (предотвращение дублей)
    intervals: {
        sync: null,
        status: null,
        globalUpdates: null
    }
};

export function setCurrentSessionId(id) {
    appState.currentSessionId = id;
}

export function updateSessions(newSessions) {
    appState.sessions = newSessions;
}

export function addMessageToCache(sessionId, messages) {
    appState.messagesCache[sessionId] = messages;
}

export function clearMessagesCache(sessionId) {
    if (sessionId) {
        delete appState.messagesCache[sessionId];
    } else {
        appState.messagesCache = {};
    }
}

export function setNewMessageIndicator(sessionId, show, isError = false) {
    if (show) {
        appState.newMessageIndicators[sessionId] = { hasNew: true, isError };
    } else {
        delete appState.newMessageIndicators[sessionId];
    }
}

export function cleanupProcessedRequests() {
    const now = Date.now();
    const FIVE_MINUTES = 5 * 60 * 1000;
    for (let [requestId, timestamp] of appState.requestProcessingTimes.entries()) {
        if (now - timestamp > FIVE_MINUTES) {
            appState.requestProcessingTimes.delete(requestId);
            appState.processedRequests.delete(requestId);
        }
    }
}