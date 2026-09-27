// static/js/admin-modelHub.js
// Model Hub tab: search Hugging Face, show fit tiers, stream download progress.
(function () {
    const input = document.getElementById('hub-search-input');
    if (!input) return;
    const btn = document.getElementById('hub-search-btn');
    const results = document.getElementById('hub-results');
    const moduleSelect = document.getElementById('hub-module-select');

    btn.addEventListener('click', doSearch);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

    function esc(s) {
        return String(s).replace(/[&<>"']/g,
            c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    async function doSearch() {
        const q = input.value.trim();
        if (!q) { results.innerHTML = note(t('hub_no_query')); return; }
        btn.disabled = true;
        btn.textContent = t('hub_searching');
        results.innerHTML = note(t('hub_searching'));
        try {
            const res = await fetchWithCSRF(`/admin/api/hub/search?q=${encodeURIComponent(q)}`);
            const data = await res.json();
            renderResults(data);
        } catch (err) {
            results.innerHTML = note(t('hub_error'));
        } finally {
            btn.disabled = false;
            btn.textContent = t('hub_search');
        }
    }

    function note(text) { return `<div class="hub-note">${esc(text)}</div>`; }

    function renderResults(data) {
        const items = (data && data.items) || [];
        if (!items.length) { results.innerHTML = note(t('hub_no_results')); return; }
        let html = '';
        for (const it of items) {
            const badges = [];
            if (it.gated) badges.push(`<span class="hub-badge hub-badge-gated">${esc(t('hub_gated'))}</span>`);
            const nc = /(nc|non-commercial|noncommercial|cc-by-nc|personal)/i.test(it.license || '');
            if (nc) badges.push(`<span class="hub-badge hub-badge-nc" title="${esc(it.license)}">${esc(t('hub_nc'))}</span>`);
            html += `<div class="hub-repo">
                <div class="hub-repo-head">
                    <span class="hub-repo-name">${esc(it.repo)}</span> ${badges.join(' ')}
                    <span class="hub-repo-meta">⬇ ${it.downloads} ⭐ ${it.likes}</span>
                </div>
                <table class="hub-files"><tbody>`;
            for (const f of it.files) {
                const repo = esc(it.repo), file = esc(f.path);
                const action = it.gated
                    ? `<td><span class="hub-badge hub-badge-gated">${esc(t('hub_gated'))}</span></td>`
                    : `<td><button class="hub-dl add-user-button" data-repo="${repo}" data-file="${file}">${esc(t('hub_download'))}</button></td>`;
                html += `<tr class="hub-file-row" data-repo="${repo}" data-file="${file}" data-gated="${it.gated ? '1' : '0'}">
                    <td class="hub-size">${f.size_mb} MB</td>
                    <td class="hub-path">${esc(f.path)}</td>
                    <td class="hub-fit hub-fit-pending">…</td>
                    ${action}
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        results.innerHTML = html;
        collectFits();
        wireDownloadButtons();
    }

    function collectFits() {
        document.querySelectorAll('.hub-file-row').forEach((row) => {
            const repo = row.dataset.repo;
            const file = row.dataset.file;
            const fitCell = row.querySelector('.hub-fit');
            fetchWithCSRF(`/admin/api/hub/fit?repo=${encodeURIComponent(repo)}&file=${encodeURIComponent(file)}&module=${moduleSelect.value}`)
                .then(r => r.json().catch(() => null))
                .then((data) => {
                    if (!data || data.status !== 'ok') {
                        fitCell.textContent = '✗';
                        fitCell.title = (data && data.error) ? data.error : t('hub_error');
                        fitCell.classList.add('hub-fit-blocked');
                        return;
                    }
                    renderFit(fitCell, data.fit);
                })
                .catch(() => { fitCell.textContent = '…'; });
        });
    }

    function renderFit(cell, fit) {
        const cls = 'hub-fit-' + fit.tier;
        cell.classList.add(cls);
        cell.title = fit.message || '';
        cell.textContent = fit.tier === 'good' ? '✓'
            : fit.tier === 'cpu_offload' ? '⚠'
            : fit.tier === 'impossible' ? '✗' : '?';
    }

    function wireDownloadButtons() {
        document.querySelectorAll('.hub-dl').forEach((btn) => {
            btn.addEventListener('click', () => startDownload(btn));
        });
    }

    async function startDownload(btn) {
        const repo = btn.dataset.repo;
        const file = btn.dataset.file;
        const row = btn.closest('.hub-file-row');
        const cells = row.querySelectorAll('td');
        btn.disabled = true;
        fetchWithCSRF('/admin/api/hub/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo: repo, file: file, module: moduleSelect.value })
        })
        .then(r => r.json())
        .then((data) => {
            if (data.status !== 'ok') {
                btn.disabled = false;
                row.querySelector('.hub-fit').textContent = '✗';
                row.querySelector('.hub-fit').title = data.error || t('hub_error');
                return;
            }
            cells[0].textContent = '';
            cells[0].innerHTML = `<span class="hub-progress-wrap">
                <span class="hub-progress-bar"><span class="hub-progress-fill" data-fill=""></span></span>
                <span class="hub-progress-text">0%</span>
                <button class="hub-cancel">${esc(t('hub_cancel'))}</button></span>`;
            btn.remove();
            pollJob(data.job_id, cells[0]);
        })
        .catch(() => { btn.disabled = false; row.querySelector('.hub-fit').textContent = '✗'; });
    }

    function pollJob(jobId, cell, everyMs) {
        everyMs = everyMs || 1000;
        const cancelBtn = cell.querySelector('.hub-cancel');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => {
                fetchWithCSRF(`/admin/api/hub/cancel/${jobId}`, { method: 'POST' }).catch(() => {});
            });
        }
        (function tick() {
            fetchWithCSRF(`/admin/api/hub/progress/${jobId}`)
                .then(r => r.json())
                .then((data) => {
                    if (data.status !== 'ok') { return; }
                    const job = data.job;
                    const fill = cell.querySelector('.hub-progress-fill');
                    const text = cell.querySelector('.hub-progress-text');
                    if (job.state === 'downloading') {
                        const pct = job.total_mb > 0 ? Math.min(99, Math.round(job.received_mb / job.total_mb * 100)) : 0;
                        fill.style.width = pct + '%';
                        text.textContent = t('hub_downloading')
                            .replace('{pct}', String(pct))
                            .replace('{total}', String(job.received_mb));
                        setTimeout(tick, everyMs);
                    } else if (job.state === 'verifying') {
                        fill.style.width = '100%';
                        text.textContent = t('hub_verifying');
                        setTimeout(tick, everyMs);
                    } else if (job.state === 'done') {
                        fill.style.width = '100%';
                        cell.innerHTML = '✓ ' + esc(t('hub_done'));
                    } else if (job.state === 'cancelled') {
                        cell.innerHTML = esc(t('hub_cancelled'));
                    } else if (job.state === 'failed') {
                        cell.innerHTML = '✗ ' + esc(t('hub_failed')) + ': ' + esc(job.error || '');
                    }
                })
                .catch(() => { cell.innerHTML = '✗ ' + esc(t('hub_failed')); });
        })();
    }
})();
