// Все запросы к серверу
import { appState } from './state.js';

export async function fetchSessions() {
    const res = await fetch('/api/sessions');
    return res.json();
}

export async function fetchMessages(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}/messages`);
    return res.json();
}

export async function fetchModelInfo(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}/model-info`);
    return res.json();
}

export async function switchSession(sessionId) {
    await fetch(`/api/sessions/${sessionId}/switch`, { method: 'POST' });
}

export async function createNewSession() {
    const res = await fetch('/api/sessions/new', { method: 'POST' });
    return res.json();
}

export async function updateSessionTitle(sessionId, title) {
    await fetch(`/api/sessions/${sessionId}/update-title`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title })
    });
}

export async function deleteSession(sessionId) {
    await fetch(`/api/sessions/${sessionId}/delete`, { method: 'POST' });
}

export async function clearHistory() {
    await fetch('/clear_history', { method: 'POST' });
}

export async function sendMessage(formData) {
    const res = await fetch('/send_message', {
        method: 'POST',
        body: formData
    });
    return res.json();
}

export async function fetchQueueStatus() {
    const res = await fetch('/api/queue/status');
    return res.json();
}

export async function cancelRequest(requestId) {
    const res = await fetch(`/api/queue/cancel/${requestId}`, { method: 'POST' });
    return res.json();
}

export async function checkResult(requestId) {
    const res = await fetch(`/api/queue/result/${requestId}`);
    return res.json();
}

export async function checkUpdates(lastCheck) {
    const res = await fetch(`/api/check-updates?last_check=${lastCheck}`);
    return res.json();
}

export async function fetchFooterText() {
    const res = await fetch('/api/footer-text');
    return res.text();
}