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
    const W = 360;
    const H = 150;
    const PAD = { left: 46, right: 8, top: 12, bottom: 22 };
    const Y_INTERVALS = 5;
    const X_TICKS = 6;

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

    // Axis ticks must read as round numbers (0, 20 000, 40 000 …), never as the
    // raw maximum (67 304). The browser locale of the page keeps grouping and
    // the compact suffix ("k", "тыс.") consistent with the active profile.
    function axisLabel(value) {
        const abs = Math.abs(value);
        if (abs >= 10000) {
            return new Intl.NumberFormat(document.documentElement.lang || undefined, {
                notation: 'compact',
                maximumFractionDigits: 1
            }).format(value);
        }
        if (Number.isInteger(value)) return formatInt(value);
        return value.toFixed(1);
    }

    function niceStep(rawStep) {
        if (!(rawStep > 0)) return 1;
        const power = Math.pow(10, Math.floor(Math.log10(rawStep)));
        const candidates = [1, 2, 2.5, 5, 10];
        for (let index = 0; index < candidates.length; index += 1) {
            if (candidates[index] * power >= rawStep) return candidates[index] * power;
        }
        return 10 * power;
    }

    function niceScale(maxValue) {
        const step = niceStep(maxValue / Y_INTERVALS);
        return { top: Math.ceil(maxValue / step) * step, step };
    }

    function formatTooltipLabel(iso) {
        return new Date(iso).toLocaleString();
    }

    function formatTickLabel(iso) {
        const date = new Date(iso);
        if (currentRange === '7d' || currentRange === '30d') {
            return date.toLocaleDateString([], { day: '2-digit', month: '2-digit' });
        }
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function seriesValues(metric, data) {
        const key = SERIES_OF[metric];
        const raw = (data.series && data.series[key]) || [];
        return raw.slice(0, data.buckets.length);
    }

    function renderChart(svg, buckets, values, options) {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        if (!buckets.length) return;

        const known = values.filter((value) => value !== null && value !== undefined);
        if (!known.some((value) => value > 0)) return;

        const scale = options.scale || 1;
        const display = values.map((value) =>
            value === null || value === undefined ? null : value * scale
        );
        const max = Math.max.apply(null, display.filter((value) => value !== null));
        const { top, step: valueStep } = niceScale(max);
        const plotW = W - PAD.left - PAD.right;
        const plotH = H - PAD.top - PAD.bottom;
        const slot = plotW / buckets.length;
        const barW = Math.max(1, slot * 0.7);
        const baseline = PAD.top + plotH;

        const y = (value) => PAD.top + plotH - (value / top) * plotH;

        // Horizontal grid: round values only, one line per tick.
        const tickCount = Math.round(top / valueStep);
        for (let index = 0; index <= tickCount; index += 1) {
            const value = index * valueStep;
            const lineY = y(value);
            svg.appendChild(el('line', {
                x1: PAD.left, y1: lineY.toFixed(1), x2: W - PAD.right, y2: lineY.toFixed(1),
                class: 'monitoring-grid'
            }));
            const label = el('text', {
                x: PAD.left - 8, y: (lineY + 3).toFixed(1), class: 'monitoring-axis', 'text-anchor': 'end'
            });
            label.textContent = options.format(value);
            svg.appendChild(label);
        }

        // Vertical grid, one line per time label under the bars.
        const tickIndexes = [];
        const tickTotal = Math.min(X_TICKS, buckets.length);
        for (let index = 0; index < tickTotal; index += 1) {
            const bucketIndex = tickTotal === 1
                ? 0
                : Math.round((index * (buckets.length - 1)) / (tickTotal - 1));
            if (tickIndexes.indexOf(bucketIndex) === -1) tickIndexes.push(bucketIndex);
        }
        tickIndexes.forEach((bucketIndex) => {
            const centerX = PAD.left + bucketIndex * slot + slot / 2;
            svg.appendChild(el('line', {
                x1: centerX.toFixed(1), y1: PAD.top, x2: centerX.toFixed(1), y2: baseline,
                class: 'monitoring-grid-v'
            }));
            const label = el('text', {
                x: centerX.toFixed(1), y: H - 6, class: 'monitoring-axis', 'text-anchor': 'middle'
            });
            label.textContent = formatTickLabel(buckets[bucketIndex]);
            svg.appendChild(label);
        });

        display.forEach((value, index) => {
            if (value === null || value === undefined || value <= 0) return;
            const bar = el('rect', {
                x: (PAD.left + index * slot + (slot - barW) / 2).toFixed(1),
                y: y(value).toFixed(1),
                width: barW.toFixed(1),
                height: Math.max(1, baseline - y(value)).toFixed(1),
                class: 'monitoring-bar'
            });
            const title = el('title');
            title.textContent = formatTooltipLabel(buckets[index]) + ' — ' + options.tooltip(values[index]);
            bar.appendChild(title);
            svg.appendChild(bar);
        });
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

            renderChart(svg, buckets, values, {
                scale: 1 / (1024 * 1024),
                format: (value) => axisLabel(value) + ' MB',
                tooltip: (value) => formatMb(value)
            });
            return;
        }

        const total = values.reduce((acc, value) => acc + (value || 0), 0);
        valueBox.textContent = hasData ? formatInt(total) : t('monitoring_no_data');
        renderChart(svg, buckets, values, {
            scale: 1,
            format: axisLabel,
            tooltip: (value) => formatInt(Math.round(value))
        });
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
