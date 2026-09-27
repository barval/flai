// static/js/admin-modelHub.js
// Model Hub tab: search Hugging Face, filter by type/context, show fit tiers,
// stream download progress.
(function () {
    const input = document.getElementById('hub-search-input');
    if (!input) return;
    const btn = document.getElementById('hub-search-btn');
    const results = document.getElementById('hub-results');
    const scanStatus = document.getElementById('hub-scan-status');
    const recalcStatus = document.getElementById('hub-recalc-status');
    const ctxSlider = document.getElementById('hub-context-slider');
    const ctxValue = document.getElementById('hub-context-value');
    const typeBoxes = {
        reasoning: document.getElementById('hub-type-reasoning'),
        multimodal: document.getElementById('hub-type-multimodal'),
        embedding: document.getElementById('hub-type-embedding')
    };

    const typeToModule = { reasoning: 'reasoning', multimodal: 'multimodal', embedding: 'embedding' };
    let lastData = null;
    let recalcBusy = false;
    let searchTimer = null;
    let searchStart = 0;

    btn.addEventListener('click', doSearch);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
    Object.values(typeBoxes).forEach(cb => cb.addEventListener('change', () => {
        if (lastData) renderResults(lastData, true);
    }));
    ctxSlider.addEventListener('input', onSliderInput);

    function esc(s) {
        return String(s).replace(/[&<>"']/g,
            c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function note(text) { return `<div class="hub-note">${esc(text)}</div>`; }

    function selectedTypes() {
        return Object.keys(typeBoxes).filter(t => typeBoxes[t].checked);
    }

    function currentContext() {
        return parseInt(ctxSlider.value, 10) || 8192;
    }

    function showStatus(el, on) { el.style.display = on ? 'block' : 'none'; }

    async function doSearch() {
        const q = input.value.trim();
        if (!q) { results.innerHTML = note(t('hub_no_query')); return; }
        btn.disabled = true;
        lastData = null;
        showStatus(recalcStatus, false);
        stopSearchTimer();
        showStatus(scanStatus, true);
        searchStart = Date.now();
        scanStatus.textContent = `🔍 ${esc(t('hub_searching_models'))} 0 ${esc(t('hub_seconds'))}`;
        searchTimer = setInterval(() => {
            const s = Math.floor((Date.now() - searchStart) / 1000);
            scanStatus.textContent = `🔍 ${esc(t('hub_searching_models'))} ${s} ${esc(t('hub_seconds'))}`;
        }, 1000);
        results.innerHTML = '';
        try {
            const res = await fetchWithCSRF(`/admin/api/hub/search?q=${encodeURIComponent(q)}`);
            const data = await res.json();
            if (data && data.items) lastData = data;
            renderResults(data);
        } catch (err) {
            results.innerHTML = note(t('hub_error'));
        } finally {
            showStatus(scanStatus, false);
            stopSearchTimer();
            btn.disabled = false;
            btn.textContent = t('hub_search');
        }
    }

    function stopSearchTimer() {
        if (searchTimer) { clearInterval(searchTimer); searchTimer = null; }
    }

    function renderResults(data, keepFits) {
        let items = (data && data.items) || [];
        const types = selectedTypes();
        if (types.length) items = items.filter(it => types.includes(it.type));
        if (!items.length) { results.innerHTML = note(t('hub_no_results')); return; }
        const sliderCtx = currentContext();
        let html = '';
        for (const it of items) {
            const badges = [];
            if (it.gated) badges.push(`<span class="hub-badge hub-badge-gated">${esc(t('hub_gated'))}</span>`);
            const nc = /(nc|non-commercial|noncommercial|cc-by-nc|personal)/i.test(it.license || '');
            if (nc) badges.push(`<span class="hub-badge hub-badge-nc" title="${esc(it.license)}">${esc(t('hub_nc'))}</span>`);
            const typeIc = it.type === 'multimodal' ? '🖼️' : it.type === 'embedding' ? '📐' : '🧠';
            const fileList = (it.files || []).slice().sort((a, b) => (a.size_mb || 0) - (b.size_mb || 0));
            html += `<div class="hub-repo">
                <div class="hub-repo-head">
                    <span class="hub-repo-name">${esc(it.repo)} ${typeIc}</span> ${badges.join(' ')}
                    ${it.arch_max_ctx ? `<span class="hub-repo-meta">${esc(t('hub_max_ctx'))}: ${it.arch_max_ctx}</span>` : ''}
                    <span class="hub-repo-meta">⬇ ${it.downloads} ⭐ ${it.likes}</span>
                </div>
                <table class="hub-files"><tbody>`;
            for (const f of fileList) {
                if (it.arch_max_ctx && it.arch_max_ctx < sliderCtx) continue;
                const repo = esc(it.repo), file = esc(f.path);
                const action = it.gated
                    ? `<td><span class="hub-badge hub-badge-gated">${esc(t('hub_gated'))}</span></td>`
                    : `<td><button class="hub-dl add-user-button" data-repo="${repo}" data-file="${file}" data-module="${typeToModule[it.type]}">${esc(t('hub_download'))}</button></td>`;
                html += `<tr class="hub-file-row" data-repo="${repo}" data-file="${file}" data-module="${typeToModule[it.type]}" data-gated="${it.gated ? '1' : '0'}">
                    <td class="hub-size">${f.size_mb} MB</td>
                    <td class="hub-path">${esc(f.path)}</td>
                    <td class="hub-fit hub-fit-pending">…</td>
                    ${action}
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        results.innerHTML = html;
        if (!keepFits) { collectFits(); wireDownloadButtons(); }
    }

    function onSliderInput() {
        ctxValue.textContent = String(currentContext());
        if (!results.querySelector('.hub-file-row')) return;
        // Reapply the context threshold to already-rendered rows, then recompute
        // the colored fit statuses with the new context.
        const sliderCtx = currentContext();
        results.querySelectorAll('.hub-file-row').forEach((row) => {
            const maxCtx = parseInt(row.dataset.maxCtx || '0', 10);
            row.style.display = (maxCtx > 0 && maxCtx < sliderCtx) ? 'none' : '';
        });
        if (recalcBusy) return;
        recalcBusy = true;
        showStatus(recalcStatus, true);
        recalcStatus.textContent = `🧮 ${esc(t('hub_recalculating'))}`;
        collectFits(() => {
            recalcBusy = false;
            showStatus(recalcStatus, false);
        });
    }

    function collectFits(done) {
        const rows = document.querySelectorAll('.hub-file-row');
        const total = rows.length;
        let pending = 0;
        if (!total) { if (done) done(); return; }
        rows.forEach((row) => {
            const repo = row.dataset.repo;
            const file = row.dataset.file;
            const module = row.dataset.module || 'multimodal';
            const fitCell = row.querySelector('.hub-fit');
            fitCell.className = 'hub-fit hub-fit-pending';
            fitCell.textContent = '…';
            pending++;
            fetchWithCSRF(`/admin/api/hub/fit?repo=${encodeURIComponent(repo)}&file=${encodeURIComponent(file)}&module=${module}&context=${currentContext()}`)
                .then(r => r.json().catch(() => null))
                .then((data) => {
                    if (!data || data.status !== 'ok') {
                        fitCell.textContent = '✗';
                        fitCell.title = (data && data.error) ? data.error : t('hub_error');
                        fitCell.classList.remove('hub-fit-pending');
                        fitCell.classList.add('hub-fit-impossible');
                        return;
                    }
                    if (data.fit && data.fit.arch_max_ctx) row.dataset.maxCtx = data.fit.arch_max_ctx;
                    renderFit(fitCell, data.fit);
                })
                .catch(() => { fitCell.textContent = '…'; })
                .finally(() => { if (--pending === 0 && done) done(); });
        });
    }

    function renderFit(cell, fit) {
        const cls = fit.platform === 'cpu'
            ? (fit.tier === 'cpu_offload' ? 'hub-fit-cpu' : 'hub-fit-impossible')
            : fit.tier === 'good' ? 'hub-fit-good'
            : fit.tier === 'cpu_offload' ? 'hub-fit-offload'
            : fit.tier === 'impossible' ? 'hub-fit-impossible' : 'hub-fit-pending';
        cell.className = 'hub-fit ' + cls;
        cell.title = fit.message || '';
        const isCpu = fit.platform === 'cpu';
        cell.textContent = fit.tier === 'good' && !isCpu ? t('hub_fit_gpu')
            : fit.tier === 'cpu_offload' ? (isCpu ? t('hub_fit_cpu') : t('hub_fit_gpu_cpu'))
            : fit.tier === 'impossible' ? t('hub_fit_impossible') : '…';
    }

    function wireDownloadButtons() {
        document.querySelectorAll('.hub-dl').forEach((btn) => {
            btn.addEventListener('click', () => startDownload(btn));
        });
    }

    async function startDownload(btn) {
        const repo = btn.dataset.repo;
        const file = btn.dataset.file;
        const module = btn.dataset.module || 'multimodal';
        const row = btn.closest('.hub-file-row');
        const cells = row.querySelectorAll('td');
        btn.disabled = true;
        fetchWithCSRF('/admin/api/hub/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo: repo, file: file, module: module })
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
                    // A transient error (progress hash not yet written) must not
                    // end the chain: reschedule and try again.
                    if (data.status !== 'ok') { setTimeout(tick, everyMs); return; }
                    const job = data.job;
                    const fill = cell.querySelector('.hub-progress-fill');
                    const text = cell.querySelector('.hub-progress-text');
                    if (job.state === 'starting') {
                        // Worker is still resolving the file: no bytes yet, but the
                        // poller must keep ticking or the bar freezes at 0%.
                        fill.style.width = '0%';
                        text.textContent = '…';
                        setTimeout(tick, everyMs);
                    } else if (job.state === 'downloading') {
                        const pct = job.total_mb > 0 ? Math.min(99, Math.round(job.received_mb / job.total_mb * 100)) : 0;
                        fill.style.width = pct + '%';
                        text.textContent = t('hub_downloading')
                            .replace('{pct}', String(pct))
                            .replace('{total}', String(job.total_mb));
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