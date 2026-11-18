// app/static/js/scheduler-panel.js
// The user's scheduled items, in the profile popup: what will happen and when.
// There is no editor on purpose — a schedule is changed by deleting it and asking again,
// so this stays a list with one action. Labels arrive through data attributes, the same
// way the API-keys section carries its own.

const SCHEDULER_ENDPOINT = '/api/scheduler/items';

function schedulerSection() {
    return document.getElementById('scheduler-section');
}

function schedulerLabel(name, fallback) {
    const section = schedulerSection();
    return (section && section.dataset[name]) || fallback;
}

function schedulerRequest(url, options = {}) {
    if (typeof fetchWithCSRF === 'function') return fetchWithCSRF(url, options);
    return fetch(url, { ...options, credentials: 'same-origin' });
}

function schedulerFormatRun(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const locale = window.CURRENT_LANG === 'en' ? 'en-GB' : 'ru-RU';
    return date.toLocaleString(locale, {
        weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
    });
}

function schedulerRow(item) {
    const row = document.createElement('div');
    row.className = 'scheduler-item';

    const icon = document.createElement('span');
    icon.className = 'scheduler-item-icon';
    icon.textContent = item.kind === 'action' ? '⚙️' : '🔔';

    const text = document.createElement('span');
    text.className = 'scheduler-item-text';

    const title = document.createElement('span');
    title.className = 'scheduler-item-title';
    title.textContent = item.title || '—';

    const when = document.createElement('span');
    when.className = 'scheduler-item-when';
    when.textContent = item.enabled === false
        ? schedulerLabel('finishedLabel', 'finished')
        : schedulerFormatRun(item.next_run_at);

    text.appendChild(title);
    text.appendChild(when);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'scheduler-item-delete';
    remove.textContent = schedulerLabel('deleteLabel', 'Delete');
    remove.addEventListener('click', () => schedulerDelete(item, remove));

    row.appendChild(icon);
    row.appendChild(text);
    row.appendChild(remove);
    return row;
}

async function schedulerLoad() {
    const container = document.getElementById('scheduler-items');
    if (!container) return;
    const response = await schedulerRequest(SCHEDULER_ENDPOINT);
    if (!response.ok) return;
    const data = await response.json();
    container.replaceChildren();
    const items = data.items || [];
    if (!items.length) {
        const empty = document.createElement('p');
        empty.className = 'api-keys-hint';
        empty.textContent = schedulerLabel('emptyLabel', 'Nothing scheduled');
        container.appendChild(empty);
        return;
    }
    items.forEach((item) => container.appendChild(schedulerRow(item)));
}

async function schedulerDelete(item, button) {
    const template = schedulerLabel('confirmTemplate', 'Delete the schedule "{title}"?');
    const question = template.replace('{title}', item.title || '—');
    if (!window.confirm(question)) return;
    button.disabled = true;
    const response = await schedulerRequest(`${SCHEDULER_ENDPOINT}/${encodeURIComponent(item.id)}/delete`, {
        method: 'POST'
    });
    if (!response.ok) {
        button.disabled = false;
        return;
    }
    schedulerLoad();
}

document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('api-keys-btn');
    const panel = document.getElementById('api-keys-panel');
    if (!button || !panel || !schedulerSection()) return;
    // Loaded when the popup opens, not on every page: the list is only ever read there.
    button.addEventListener('click', () => {
        if (!panel.hidden) schedulerLoad();
    });
});

window.schedulerLoad = schedulerLoad;
