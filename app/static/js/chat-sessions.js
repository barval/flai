// app/static/js/chat-sessions.js
// Session management functions
function setNewMessageIndicator(sessionId, show) {
if (show) {
newMessageIndicators[sessionId] = true;
} else {
delete newMessageIndicators[sessionId];
}
updateSessionsListFromData();
}
function loadSessionsFromServer() {
return fetch('/api/sessions')
.then(res => {
if (!res.ok) {
throw new Error(`HTTP error ${res.status}`);
}
return res.json();
})
.then(sessions => {
let updated = false;
sessions.forEach(s => {
if (!sessionsData[s.id]) {
sessionsData[s.id] = {
title: s.title,
updated_at: s.updated_at,
message_count: s.message_count
};
updated = true;
} else {
if (sessionsData[s.id].title !== s.title) {
sessionsData[s.id].title = s.title;
sessionsData[s.id].updated_at = s.updated_at;
sessionsData[s.id].message_count = s.message_count;
updated = true;
} else if (sessionsData[s.id].updated_at !== s.updated_at) {
sessionsData[s.id].updated_at = s.updated_at;
sessionsData[s.id].message_count = s.message_count;
updated = true;
} else if (sessionsData[s.id].message_count !== s.message_count) {
sessionsData[s.id].message_count = s.message_count;
updated = true;
}
}
const prevUnread = newMessageIndicators[s.id] ? true : false;
const newUnread = s.has_unread ? true : false;
if (prevUnread !== newUnread) {
updated = true;
}
if (s.has_unread) {
newMessageIndicators[s.id] = true;
} else {
delete newMessageIndicators[s.id];
}
});
Object.keys(sessionsData).forEach(id => {
if (!sessions.find(s => s.id === id)) {
delete sessionsData[id];
delete newMessageIndicators[id];
updated = true;
}
});
if (updated) {
updateSessionsList(sessions);
}
return sessions;
})
.catch(err => {
console.error('Error loading sessions:', err);
return [];
});
}
function updateSessionsListFromData() {
if (sessionsUpdateTimeout) {
clearTimeout(sessionsUpdateTimeout);
}
sessionsUpdateTimeout = setTimeout(() => {
const sessions = Object.keys(sessionsData).map(id => ({
id: id,
title: sessionsData[id].title,
updated_at: sessionsData[id].updated_at,
message_count: sessionsData[id].message_count
}));
updateSessionsList(sessions);
sessionsUpdateTimeout = null;
}, 100);
}
function updateSessionsList(sessions) {
const sessionsList = document.getElementById('sessions-list');
if (!sessionsList) return;
const currentActiveId = currentSessionId;
sessions.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
let html = '';
sessions.forEach(s => {
const isActive = s.id === currentActiveId ? 'active' : '';
const dateStr = s.updated_at ? formatFullDateTime(s.updated_at) : '';
// Build status icons with proper priority
// FIXED: Transcribing takes TOP priority - shows during audio transcription
// even if task is queued (transcribing happens BEFORE queue processing)
let statusIcons = '';
const transcribing = localTranscribingSessions[s.id];
const info = sessionQueueInfo[s.id];
// CRITICAL FIX: Transcribing icon has HIGHEST priority
// It should show during actual transcription, regardless of queue status
if (transcribing) {
statusIcons = '<span class="session-status-icon transcribing blink" title="' + t('transcribing') + '">🎤</span>';
} else {
// Only show queue status if NOT transcribing
let queueStatusShown = false;
if (info && info.processing) {
statusIcons = '<span class="session-status-icon processing blink" title="' + t('processing') + '">⚡</span>';
queueStatusShown = true;
} else if (info && info.queued > 0) {
const count = info.queued > 1 ? ' ' + info.queued : '';
statusIcons = '<span class="session-status-icon queued" title="' + t('queued') + '">⏳' + count + '</span>';
queueStatusShown = true;
}
// Unread indicator (only if no other status and not active session)
if (!queueStatusShown && newMessageIndicators[s.id] && s.id !== currentActiveId) {
statusIcons = '<span class="session-status-icon unread blink" title="' + t('new_response') + '">✉️</span>';
}
}
// TTS icon (independent, can show with other statuses)
let ttsIcon = '';
if (currentPlayingSessionId === s.id) {
ttsIcon = '<span class="session-status-icon tts playing" title="' + t('speak') + '">🗣️</span>';
}
html += `
<div class="session-item ${isActive}" data-session-id="${s.id}" data-session-title="${escapeHtml(s.title)}">
<div class="session-content">
<div class="session-info">
<div class="session-title">
${ttsIcon}${statusIcons}
📝 ${escapeHtml(s.title)}
</div>
<div class="session-date">📅 ${dateStr} [${s.message_count}]</div>
</div>
<button class="delete-session-button" title="${t('delete_session')}">🗑️</button>
</div>
</div>
`;
});
sessionsList.innerHTML = html;
document.getElementById('sessions-count').textContent = sessions.length;
attachSessionEventHandlers();
}
function attachSessionEventHandlers() {
document.querySelectorAll('.session-item').forEach(el => {
el.addEventListener('click', function(e) {
if (e.target.closest('.delete-session-button')) return;
const sessionId = this.dataset.sessionId;
if (sessionId === currentSessionId) return;
setNewMessageIndicator(sessionId, false);
document.querySelectorAll('.session-item').forEach(i => i.classList.remove('active'));
this.classList.add('active');
switchSession(sessionId);
});
});
document.querySelectorAll('.delete-session-button').forEach(btn => {
btn.addEventListener('click', function(e) {
e.stopPropagation();
const sessionItem = this.closest('.session-item');
const sessionId = sessionItem.dataset.sessionId;
const sessionTitle = sessionItem.dataset.sessionTitle;
const sessionDate = sessionItem.querySelector('.session-date').textContent;
deleteSession(sessionId, sessionTitle, sessionDate);
});
});
}
function createNewSession() {
fetch('/api/sessions/new', { method: 'POST' })
.then(res => {
if (!res.ok) {
throw new Error(`HTTP error ${res.status}`);
}
return res.json();
})
.then(data => {
sessionsData[data.id] = {
title: data.title,
updated_at: new Date().toISOString(),
message_count: 0
};
document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
currentSessionId = data.id;
loadSessionsFromServer().then(() => {
document.getElementById('chat-messages').innerHTML = '';
updateMessageCount();
defaultModelName = 'qwen3-vl:8b-instruct';
setNewMessageIndicator(data.id, false);
});
})
.catch(err => {
console.error('Error creating new session:', err);
alert(t('error') + ': ' + err.message);
});
}
function updateSessionTitle(sessionId, newTitle) {
if (sessionsData[sessionId]) {
sessionsData[sessionId].title = newTitle;
sessionsData[sessionId].updated_at = new Date().toISOString();
}
const sessionItem = document.querySelector('.session-item[data-session-id="' + sessionId + '"]');
if (!sessionItem) return;
const titleElement = sessionItem.querySelector('.session-title');
if (titleElement) titleElement.innerHTML = '📝 ' + escapeHtml(newTitle);
const now = new Date();
const formattedDate = formatFullDateTime(now.toISOString());
const dateElement = sessionItem.querySelector('.session-date');
if (dateElement) dateElement.textContent = '📅 ' + formattedDate;
const sessionsList = document.getElementById('sessions-list');
if (sessionsList.firstChild !== sessionItem) {
sessionsList.insertBefore(sessionItem, sessionsList.firstChild);
}
}
function deleteSession(sessionId, sessionTitle, sessionDate) {
// Stop TTS if playing in this session
if (currentPlayingSessionId === sessionId) {
if (typeof window.resetTtsState === 'function') {
window.resetTtsState();
}
}
const confirmMessage = formatString(t('delete_session_confirm'), {
title: sessionTitle,
date: sessionDate
});
if (!confirm(confirmMessage)) return;
for (let [id, req] of Object.entries(pendingRequests)) {
if (req.sessionId === sessionId && !req.processed) {
pendingRequests[id].processed = true;
}
}
delete newMessageIndicators[sessionId];
delete localTranscribingSessions[sessionId];
fetch('/api/sessions/' + sessionId + '/delete', { method: 'POST' })
.then(res => res.json())
.then(data => {
if (data.status === 'ok') {
delete sessionsData[sessionId];
const sessionItem = document.querySelector('.session-item[data-session-id="' + sessionId + '"]');
if (sessionItem) sessionItem.remove();
const sessionsCount = document.querySelectorAll('.session-item').length;
document.getElementById('sessions-count').textContent = sessionsCount;
if (sessionId === currentSessionId) {
const remainingSessions = document.querySelectorAll('.session-item');
if (remainingSessions.length > 0) {
switchSession(remainingSessions[0].dataset.sessionId);
} else {
setTimeout(() => createNewSession(), 50);
}
}
}
})
.catch(err => alert(t('error') + ': ' + err.message));
}
function switchSession(sessionId) {
if (!sessionId) {
console.error('switchSession called with empty sessionId');
return;
}
const statusCounter = document.getElementById('status-counter');
if (statusCounter) {
statusCounter.innerHTML = '⏳ ' + t('loading');
}
fetch('/api/sessions/' + sessionId + '/switch', { method: 'POST' })
.then(res => res.json())
.then(() => {
currentSessionId = sessionId;
loadMessages(sessionId).catch(err => {
console.error('Error loading messages in switchSession:', err);
if (statusCounter) {
statusCounter.innerHTML = '❌';
setTimeout(() => window.updateStatusCounter(), 2000);
}
}).finally(() => {
window.updateStatusCounter();
});
document.querySelectorAll('.session-item').forEach(el => {
if (el.dataset.sessionId === sessionId) {
el.classList.add('active');
} else {
el.classList.remove('active');
}
});
updateSessionsListFromData();
})
.catch(err => {
console.error('Error switching session:', err);
if (statusCounter) {
statusCounter.innerHTML = '❌';
setTimeout(() => window.updateStatusCounter(), 2000);
}
});
}
function updateLastVisit(sessionId) {
if (!sessionId) return;
fetch(`/api/sessions/${sessionId}/visit`, { method: 'POST' })
.catch(err => console.error('Error updating last_visit:', err));
}
// ===== Collapsible sidebar for mobile =====
function initCollapsibleSessions() {
const sidebar = document.querySelector('.sessions-sidebar');
const collapseToggle = document.getElementById('collapse-toggle-mobile');
if (!sidebar || !collapseToggle) return;
// Remove old click handler if exists
collapseToggle.removeEventListener('click', toggleSessions);
// Add click handler to collapse toggle
collapseToggle.addEventListener('click', toggleSessions);
// Restore state from localStorage
const login = window.CURRENT_USER_LOGIN;
if (login) {
const collapsed = localStorage.getItem(`sidebar_collapsed_${login}`);
if (collapsed === 'true') {
sidebar.classList.add('collapsed');
} else {
sidebar.classList.remove('collapsed');
}
}
// On mobile, start with sidebar collapsed by default
if (window.innerWidth <= 768 && login) {
const collapsed = localStorage.getItem(`sidebar_collapsed_${login}`);
if (collapsed !== 'false') {
sidebar.classList.add('collapsed');
}
}
// Update collapse icon
updateCollapseIcon();
}
function toggleSessions(e) {
e.stopPropagation();
const sidebar = document.querySelector('.sessions-sidebar');
if (!sidebar) return;
sidebar.classList.toggle('collapsed');
const login = window.CURRENT_USER_LOGIN;
if (login) {
localStorage.setItem(`sidebar_collapsed_${login}`, sidebar.classList.contains('collapsed'));
}
// Update collapse icon
updateCollapseIcon();
}
// Update collapse icon based on sidebar state
function updateCollapseIcon() {
const sidebar = document.querySelector('.sessions-sidebar');
const collapseIcon = document.getElementById('collapse-icon');
if (sidebar && collapseIcon) {
if (sidebar.classList.contains('collapsed')) {
collapseIcon.textContent = '➡️';
} else {
collapseIcon.textContent = '⬇️';
}
}
}