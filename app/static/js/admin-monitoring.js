// app/static/js/admin-monitoring.js
// Monitoring tab: one payload, eight hand-made SVG charts (no chart library).
(function () {
    'use strict';

    const SERIES_OF = {
        storage: 'storage_bytes',
        sessions: 'sessions',
        messages: 'messages',
        tasks: 'tasks',
        outgoing_tokens: 'outgoing_tokens',
        incoming_tokens: 'incoming_tokens',
        files: 'files',
        documents: 'documents'
    };
    const METRICS = Object.keys(SERIES_OF);
    const POLL_MS = 60000;
    const NS = 'http://www.w3.org/2000/svg';
    const W = 320;
    const H = 120;
    const PAD = { left: 44, right: 8, top: 10, bottom: 20 };

    const select = document.getElementById('monitoring-user');
    const rangeBox = document.getElementById('monitoring-range');
    const errorBox = document.getElementById('monitoring-error');
    if (!select || !rangeBox || !document.getElementById('monitoring-cards')) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    let currentRange = '24h';
    let currentUser = 'all';

    function isActive() {
        const tab = document.getElementById('monitoring-tab');
        return !!tab && tab.classList.contains('active');
    }

    function el(name, attrs) {
        const node = document.createElementNS(NS, name);
        Object.keys(attrs || {}).forEach((key) => node.setAttribute(key, attrs[key]));
        return node;
    }

    function formatMb(bytes) {
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function formatInt(value) {
        return Number(value || 0).toLocaleString();
    }

    function formatLabel(iso, long) {
        const date = new Date(iso);
        if (long) return date.toLocaleString();
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function seriesValues(metric, data) {
        const key = SERIES_OF[metric];
        const raw = (data.series && data.series[key]) || [];
        return raw.slice(0, data.buckets.length);
    }

    function renderChart(svg, buckets, values, formatter) {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        if (!buckets.length) return;

        const known = values.filter((value) => value !== null && value !== undefined);
        if (!known.some((value) => value > 0)) return;
        const max = Math.max.apply(null, known);
        const top = max > 0 ? max : 1;
        const plotW = W - PAD.left - PAD.right;
        const plotH = H - PAD.top - PAD.bottom;
        const step = buckets.length > 1 ? plotW / (buckets.length - 1) : 0;

        const x = (index) => PAD.left + index * step;
        const y = (value) => PAD.top + plotH - (value / top) * plotH;

        [0, 0.5, 1].forEach((fraction) => {
            const value = top * fraction;
            const line = el('line', {
                x1: PAD.left, y1: y(value), x2: W - PAD.right, y2: y(value),
                class: 'monitoring-grid'
            });
            svg.appendChild(line);
            const label = el('text', {
                x: PAD.left - 6, y: y(value) + 3, class: 'monitoring-axis', 'text-anchor': 'end'
            });
            label.textContent = formatter(value);
            svg.appendChild(label);
        });

        let path = '';
        let pen = false;
        values.forEach((value, index) => {
            if (value === null || value === undefined) {
                pen = false;
                return;
            }
            path += (pen ? ' L ' : ' M ') + x(index).toFixed(1) + ' ' + y(value).toFixed(1);
            pen = true;
        });
        if (path) {
            svg.appendChild(el('path', { d: path, class: 'monitoring-line' }));
        }

        values.forEach((value, index) => {
            if (value === null || value === undefined) return;
            const point = el('circle', {
                cx: x(index).toFixed(1), cy: y(value).toFixed(1), r: 5,
                class: 'monitoring-point'
            });
            const title = el('title');
            title.textContent = formatLabel(buckets[index], true) + ' — ' + formatter(value);
            point.appendChild(title);
            svg.appendChild(point);
        });

        const first = el('text', { x: PAD.left, y: H - 5, class: 'monitoring-axis', 'text-anchor': 'start' });
        first.textContent = formatLabel(buckets[0], false);
        svg.appendChild(first);

        const last = el('text', { x: W - PAD.right, y: H - 5, class: 'monitoring-axis', 'text-anchor': 'end' });
        last.textContent = formatLabel(buckets[buckets.length - 1], false);
        svg.appendChild(last);
    }

    function renderCard(metric, data) {
        const svg = document.getElementById('monitoring-chart-' + metric);
        const valueBox = document.getElementById('monitoring-value-' + metric);
        if (!svg || !valueBox) return;

        const values = seriesValues(metric, data);
        const buckets = data.buckets || [];
        const hasData = values.some((value) => value !== null && value !== undefined && value > 0);

        if (metric === 'storage') {
            const storage = data.storage || {};
            const limit = storage.limit_bytes || 0;
            const used = storage.current_bytes || 0;
            const percent = limit ? ((used / limit) * 100).toFixed(1) : '0.0';
            valueBox.textContent = hasData || storage.since
                ? formatMb(used) + ' / ' + formatMb(limit) + ' (' + percent + '%)'
                : t('monitoring_no_data');

            const sinceBox = document.getElementById('monitoring-since-storage');
            if (sinceBox) {
                if (storage.since) {
                    sinceBox.textContent = t('monitoring_since').replace(
                        '{date}',
                        new Date(storage.since).toLocaleString()
                    );
                    sinceBox.hidden = false;
                } else {
                    sinceBox.hidden = true;
                }
            }

            renderChart(svg, buckets, values, (value) => formatMb(value));
            return;
        }

        const total = values.reduce((acc, value) => acc + (value || 0), 0);
        valueBox.textContent = hasData ? formatInt(total) : t('monitoring_no_data');
        renderChart(svg, buckets, values, (value) => formatInt(Math.round(value)));
    }

    function render(data) {
        METRICS.forEach((metric) => renderCard(metric, data));
    }

    function populateUsers(users) {
        const wanted = currentUser;
        select.innerHTML = '';
        const all = document.createElement('option');
        all.value = 'all';
        all.textContent = t('monitoring_all_users');
        select.appendChild(all);
        (users || []).forEach((user) => {
            const option = document.createElement('option');
            option.value = user.login;
            option.textContent = user.login;
            select.appendChild(option);
        });
        if (wanted !== 'all' && !users.some((user) => user.login === wanted)) {
            currentUser = 'all';
        }
        select.value = currentUser;
    }

    function showError(message) {
        if (!errorBox) return;
        errorBox.textContent = message;
        errorBox.hidden = false;
    }

    function hideError() {
        if (errorBox) errorBox.hidden = true;
    }

    async function loadMonitoring() {
        hideError();
        try {
            const url = '/admin/api/monitoring?user=' + encodeURIComponent(currentUser) +
                '&range=' + encodeURIComponent(currentRange);
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            populateUsers(data.users || []);
            render(data);
        } catch (err) {
            showError(t('monitoring_load_error'));
        }
    }

    rangeBox.querySelectorAll('.monitoring-range-btn').forEach((button) => {
        button.addEventListener('click', () => {
            rangeBox.querySelectorAll('.monitoring-range-btn').forEach((other) => other.classList.remove('active'));
            button.classList.add('active');
            currentRange = button.dataset.range;
            loadMonitoring();
        });
    });

    select.addEventListener('change', () => {
        currentUser = select.value;
        loadMonitoring();
    });

    const tabButton = document.querySelector('.admin-tab[data-tab="monitoring"]');
    if (tabButton) tabButton.addEventListener('click', loadMonitoring);

    document.addEventListener('DOMContentLoaded', () => {
        if (isActive()) loadMonitoring();
    });

    setInterval(() => {
        if (isActive()) loadMonitoring();
    }, POLL_MS);

    window.Monitoring = { init: loadMonitoring };
})();
