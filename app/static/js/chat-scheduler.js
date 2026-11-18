// app/static/js/chat-scheduler.js
// A reminder arrives twice: as a message in its own chat and as a popup on top of
// whatever is open. The popup is only a notification — the message is already stored,
// so closing it loses nothing, and the next reminder waits its turn instead of
// replacing the one being read.

const schedulerPopupQueue = [];
let schedulerPopupShown = false;

function onSchedulerPopup(data) {
    if (!data || !data.text) return;
    schedulerPopupQueue.push(data);
    showNextSchedulerPopup();
}

function showNextSchedulerPopup() {
    if (schedulerPopupShown) return;
    const modal = document.getElementById('scheduler-popup');
    if (!modal) return;
    const next = schedulerPopupQueue.shift();
    if (!next) return;

    const title = document.getElementById('scheduler-popup-title');
    const text = document.getElementById('scheduler-popup-text');
    if (title) {
        const label = String(next.title || '').trim();
        title.textContent = label;
        title.hidden = label === '';
    }
    if (text) {
        text.textContent = String(next.text || '');
    }

    modal.hidden = false;
    schedulerPopupShown = true;
    const ok = document.getElementById('scheduler-popup-ok');
    if (ok && typeof ok.focus === 'function') ok.focus();
}

function closeSchedulerPopup() {
    const modal = document.getElementById('scheduler-popup');
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    schedulerPopupShown = false;
    showNextSchedulerPopup();
}

document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('scheduler-popup');
    if (!modal) return;
    modal.querySelectorAll('[data-scheduler-close]').forEach(function (element) {
        element.addEventListener('click', closeSchedulerPopup);
    });
});

document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeSchedulerPopup();
});

window.onSchedulerPopup = onSchedulerPopup;
window.closeSchedulerPopup = closeSchedulerPopup;
