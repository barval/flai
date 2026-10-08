// static/js/admin-modelHub.js
// Model Hub tab: search Hugging Face, filter by type/context, show fit tiers,
// stream download progress.
(function () {
    const input = document.getElementById('hub-search-input');
    if (!input) return;
    const btn = document.getElementById('hub-search-btn');
    const results = document.getElementById('hub-results');
    const activeDownloads = document.getElementById('hub-active-downloads');
    const scanStatus = document.getElementById('hub-scan-status');
    const recalcStatus = document.getElementById('hub-recalc-status');
    const ctxSlider = document.getElementById('hub-context-slider');
    const ctxValue = document.getElementById('hub-context-value');
    const CTX_MIN = 1024;
    const CTX_MAX = 262144;
    const typeBoxes = {
        reasoning: document.getElementById('hub-type-reasoning'),
        multimodal: document.getElementById('hub-type-multimodal'),
        embedding: document.getElementById('hub-type-embedding')
    };

    const typeToModule = { reasoning: 'reasoning', multimodal: 'multimodal', embedding: 'embedding' };
    const ACTIVE_DOWNLOADS_KEY = 'flai.modelHub.activeDownloads';
    let lastData = null;
    let installedList = [];
    let recalcBusy = false;
    let searchTimer = null;
    let recalcTimer = null;
    let searchStart = 0;

    btn.addEventListener('click', doSearch);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
    Object.values(typeBoxes).forEach(cb => cb.addEventListener('change', () => {
        if (lastData) renderResults(lastData, true);
    }));
    ctxSlider.addEventListener('input', () => {
        ctxValue.value = ctxSlider.value;
        refreshFits();
    });
    ctxValue.addEventListener('input', () => {
        const digits = ctxValue.value.replace(/\D+/g, '');
        ctxValue.value = digits;
        const n = parseInt(digits, 10);
        if (n) ctxSlider.value = Math.max(CTX_MIN, Math.min(n, CTX_MAX));
    });
    ctxValue.addEventListener('change', () => {
        const n = parseInt(ctxValue.value.replace(/\D+/g, ''), 10);
        if (!n) { ctxValue.value = ctxSlider.value; return; }
        const ctx = Math.max(CTX_MIN, Math.min(n, CTX_MAX));
        ctxSlider.value = ctx;
        ctxValue.value = String(ctx);
        refreshFits();
    });
    resumeActiveDownloads();

    function esc(s) {
        return String(s).replace(/[&<>"']/g,
            c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function fmtNum(n) {
        return (Number(n) || 0).toLocaleString();
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
            const res = await fetchWithCSRF(`/admin/api/hub/search?q=${encodeURIComponent(q)}&context=${currentContext()}`);
            const data = await res.json();
            if (data && data.items) { lastData = data; installedList = (data.installed || []); }
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

    function _stopRecalcTimer() {
        if (recalcTimer) { clearInterval(recalcTimer); recalcTimer = null; }
    }

    function renderResults(data, keepFits) {
        let items = (data && data.items) || [];
        const hasFits = !!(data && data.fits_computed);
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
                    ${it.arch_max_ctx ? `<span class="hub-repo-meta">${esc(t('hub_max_ctx'))}: ${fmtNum(it.arch_max_ctx)}</span>` : ''}
                    <span class="hub-repo-meta">⬇ ${fmtNum(it.downloads)} ⭐ ${fmtNum(it.likes)}</span>
                </div>
                <table class="hub-files"><tbody>`;
            for (const f of fileList) {
                if (it.arch_max_ctx && it.arch_max_ctx < sliderCtx) continue;
                const repo = esc(it.repo), file = esc(f.path);
                const fileName = f.path.split('/').pop();
                const isInstalled = installedList.includes(fileName);
                const companionOptions = (f.companion_choices || []).flatMap(group => group.options || []);
                const companionChoiceHtml = companionOptions.length
                    ? `<div class="hub-companion-choice">${companionOptions.map(option => `
                        <label><input type="radio" name="hub-draft-${esc(it.repo)}-${esc(f.path)}" value="${esc(option.path)}" data-companion-choice data-size-mb="${Number(option.size_mb) || 0}" ${option.default ? 'checked' : ''} ${isInstalled ? 'disabled' : ''}> ${esc(option.path.split('/').pop())} (${fmtNum(Math.ceil(option.size_mb))} ${t('MB')})</label>`).join('')}</div>`
                    : '';
                const action = it.gated
                    ? `<td><span class="hub-badge hub-badge-gated">${esc(t('hub_gated'))}</span></td>`
                    : isInstalled
                        ? `<td><span class="hub-installed">✓ ${esc(t('hub_installed'))}</span> <button class="hub-del add-user-button" data-filename="${esc(fileName)}">🗑 ${esc(t('hub_uninstall'))}</button></td>`
                        : `<td><button class="hub-dl add-user-button" data-repo="${repo}" data-file="${file}" data-module="${typeToModule[it.type]}">${esc(t('hub_download'))}</button></td>`;
                const fitAttr = f.fit ? ` title="${esc(f.fit.message || '')}"` : '';
                html += `<tr class="hub-file-row" data-repo="${repo}" data-file="${file}" data-module="${typeToModule[it.type]}" data-gated="${it.gated ? '1' : '0'}" data-max-ctx="${(f.fit && f.fit.arch_max_ctx) || ''}" data-companion-required-mb="${Number(f.companion_required_mb) || 0}">
                    <td class="hub-size">${fmtNum(Math.ceil(f.size_mb || 0))} ${t('MB')}</td>
                    <td class="hub-path">${esc(f.path)}${f.companion_mb > 0
                        ? `<div class="hub-aux-note">${esc(t('hub_service_files').replace('{n}', fmtNum(Math.ceil(f.companion_mb))))}</div>`
                        : companionOptions.length ? `<div class="hub-aux-note">${esc(t('hub_service_files').replace('{n}', fmtNum(Math.ceil(f.companion_mb))))}</div>` : ''}${companionChoiceHtml}</td>
                    <td class="hub-fit ${f.fit ? fitTierClass(f.fit) : 'hub-fit-pending'}"${fitAttr}>${f.fit ? esc(fitTierLabel(f.fit)) : '…'}</td>
                    ${action}
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        results.innerHTML = html;
        wireDownloadButtons();
        if (!keepFits) {
            if (!hasFits) recalcFits('hub_calculating');
        }
    }

    function recalcFits(messageKey) {
        if (recalcBusy) return;
        if (!results.querySelector('.hub-file-row')) return;
        recalcBusy = true;
        showStatus(recalcStatus, true);
        const baseText = esc(t(messageKey));
        recalcStatus.textContent = `🧮 ${baseText}`;
        const startedAt = Date.now();
        const suffix = esc(t('hub_seconds'));
        _stopRecalcTimer();
        recalcTimer = setInterval(() => {
            const secs = Math.floor((Date.now() - startedAt) / 1000);
            if (secs >= 1) recalcStatus.textContent = `🧮 ${baseText} (${secs}${suffix})`;
        }, 1000);
        collectFits(() => {
            _stopRecalcTimer();
            recalcBusy = false;
            showStatus(recalcStatus, false);
        });
    }

    function refreshFits() {
        if (!results.querySelector('.hub-file-row')) return;
        // Reapply the context threshold to already-rendered rows before
        // recomputing the colored fit statuses with the new context.
        const sliderCtx = currentContext();
        results.querySelectorAll('.hub-file-row').forEach((row) => {
            const maxCtx = parseInt(row.dataset.maxCtx || '0', 10);
            row.style.display = (maxCtx > 0 && maxCtx < sliderCtx) ? 'none' : '';
        });
        recalcFits('hub_recalculating');
    }

    function collectFits(done) {
        const rows = document.querySelectorAll('.hub-file-row');
        if (!rows.length) { if (done) done(); return; }
        // One /fit-all request per repo instead of one /fit per file: with a
        // single-process server a 50-repo list used to queue hundreds of
        // sequential requests and keep "Computing fit…" alive for minutes.
        const groups = {};
        rows.forEach((row) => {
            const repo = row.dataset.repo;
            (groups[repo] = groups[repo] || []).push(row);
        });
        let pending = Object.keys(groups).length;
        const finish = () => { if (--pending === 0 && done) done(); };
        Object.entries(groups).forEach(([repo, group]) => {
            const module = group[0].dataset.module || 'multimodal';
            group.forEach((row) => {
                const fitCell = row.querySelector('.hub-fit');
                fitCell.className = 'hub-fit hub-fit-pending';
                fitCell.textContent = '…';
            });
            const context = currentContext();
            fetchWithCSRF(`/admin/api/hub/fit-all?repo=${encodeURIComponent(repo)}&module=${module}&context=${context}`)
                .then(r => r.json().catch(() => null))
                .then((data) => {
                    const byFile = (data && data.status === 'ok' && data.fits) || {};
                    group.forEach((row) => {
                        const fitCell = row.querySelector('.hub-fit');
                        const fit = byFile[row.dataset.file];
                        if (!fit || fit.error) {
                            fitCell.textContent = '✗';
                            fitCell.title = fit && fit.error ? fit.error : t('hub_error');
                            fitCell.classList.remove('hub-fit-pending');
                            fitCell.classList.add('hub-fit-impossible');
                            return;
                        }
                        if (fit.arch_max_ctx) row.dataset.maxCtx = fit.arch_max_ctx;
                        renderFit(fitCell, fit);
                    });
                })
                .catch(() => {
                    group.forEach((row) => { row.querySelector('.hub-fit').textContent = '…'; });
                })
                .finally(finish);
        });
    }

function fitTierClass(fit) {
        if (fit.error) return 'hub-fit-impossible';
        const isCpu = fit.platform === 'cpu';
        if (isCpu) return fit.tier === 'cpu_offload' ? 'hub-fit-cpu' : 'hub-fit-impossible';
        if (fit.tier === 'good') return 'hub-fit-good';
        if (fit.tier === 'cpu_offload') return 'hub-fit-offload';
        if (fit.tier === 'impossible') return 'hub-fit-impossible';
        return 'hub-fit-pending';
    }

    function fitTierLabel(fit) {
        if (fit.error) return '✗';
        const isCpu = fit.platform === 'cpu';
        if (fit.tier === 'good' && !isCpu) return t('hub_fit_gpu');
        if (fit.tier === 'cpu_offload') return isCpu ? t('hub_fit_cpu') : t('hub_fit_gpu_cpu');
        if (fit.tier === 'impossible') return t('hub_fit_impossible');
        return '…';
    }

    function renderFit(cell, fit) {
        cell.className = 'hub-fit ' + fitTierClass(fit);
        cell.title = fit.message || '';
        cell.textContent = fitTierLabel(fit);
    }

    function wireDownloadButtons() {
        document.querySelectorAll('.hub-dl').forEach((btn) => {
            btn.addEventListener('click', () => startDownload(btn));
        });
        document.querySelectorAll('.hub-del').forEach((btn) => {
            btn.addEventListener('click', () => deleteDownloadedFile(btn, btn.dataset.filename));
        });
        document.querySelectorAll('.hub-companion-choice input').forEach((input) => {
            input.addEventListener('change', () => {
                const row = input.closest('.hub-file-row');
                const chosen = row.querySelector('.hub-companion-choice input:checked');
                const total = Number(row.dataset.companionRequiredMb || 0) + Number(chosen?.dataset.sizeMb || 0);
                const note = row.querySelector('.hub-aux-note');
                if (note) note.textContent = t('hub_service_files').replace('{n}', fmtNum(Math.ceil(total)));
            });
        });
    }

    function deleteDownloadedFile(btn, filename) {
        const msg = t('hub_delete_confirm').replace('{filename}', filename);
        if (!confirm(msg)) return;
        btn.disabled = true;
        fetchWithCSRF('/admin/api/hub/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: filename })
        })
        .then(r => r.json())
        .then((data) => {
            if (data.status === 'ok') {
                installedList = installedList.filter(b => b !== filename);
                if (lastData) renderResults(lastData, true);
            } else {
                btn.disabled = false;
                alert(data.error || t('hub_error'));
            }
        })
        .catch(() => { btn.disabled = false; alert(t('hub_error')); });
    }

    function checkHubReachability() {
        const banner = document.getElementById('hub-offline-banner');
        if (!banner) return;
        const showBanner = () => {
            banner.innerHTML = '⚠️ ' + esc(t('hub_no_hf_access')) + '. ' + esc(t('hub_manual_upload'));
            banner.style.display = 'block';
        };
        fetch('/admin/api/hub/reachability', { credentials: 'same-origin' })
            .then(r => r.json())
            .then((data) => {
                if (data && data.reachable) banner.style.display = 'none';
                else showBanner();
            })
            .catch(showBanner);
    }

    function getActiveDownloads() {
        try {
            return JSON.parse(sessionStorage.getItem(ACTIVE_DOWNLOADS_KEY) || '[]');
        } catch (_) {
            return [];
        }
    }

    function saveActiveDownloads(jobs) {
        sessionStorage.setItem(ACTIVE_DOWNLOADS_KEY, JSON.stringify(jobs));
    }

    function rememberActiveDownload(job) {
        const jobs = getActiveDownloads().filter(item => item.job_id !== job.job_id);
        jobs.push(job);
        saveActiveDownloads(jobs);
    }

    function forgetActiveDownload(jobId) {
        saveActiveDownloads(getActiveDownloads().filter(item => item.job_id !== jobId));
    }

    function createDownloadRow(job) {
        const row = document.createElement('div');
        row.className = 'hub-dl-job';
        const name = document.createElement('span');
        name.className = 'hub-dl-job-name';
        name.textContent = job.file;
        const wrap = document.createElement('span');
        wrap.className = 'hub-progress-wrap';
        const bar = document.createElement('span');
        bar.className = 'hub-progress-bar';
        const fill = document.createElement('span');
        fill.className = 'hub-progress-fill';
        bar.appendChild(fill);
        const text = document.createElement('span');
        text.className = 'hub-progress-text';
        text.textContent = '…';
        wrap.append(bar, text);
        const cancel = document.createElement('button');
        cancel.className = 'hub-cancel add-user-button';
        cancel.textContent = t('hub_cancel');
        cancel.dataset.job = job.job_id;
        row.append(name, wrap, cancel);
        activeDownloads.appendChild(row);
        activeDownloads.style.display = 'block';
        return { row, cancel };
    }

    function resumeActiveDownloads() {
        const jobs = getActiveDownloads();
        if (!activeDownloads || !jobs.length) return;
        jobs.forEach(job => {
            const ui = createDownloadRow(job);
            pollJob(job.job_id, ui.row, ui.cancel, job.repo, job.file, job.module);
        });
    }

    async function startDownload(btn) {
        const repo = btn.dataset.repo;
        const file = btn.dataset.file;
            const module = btn.dataset.module || 'multimodal';
            const row = btn.closest('.hub-file-row');
        const companionPaths = row
            ? Array.from(row.querySelectorAll('[data-companion-choice]:checked')).map(input => input.value)
            : [];
        btn.disabled = true;
        fetchWithCSRF('/admin/api/hub/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                repo: repo,
                file: file,
                module: module,
                companions: companionPaths
            })
        })
        .then(r => r.json())
        .then((data) => {
            if (data.status !== 'ok') {
                btn.disabled = false;
                row.querySelector('.hub-fit').textContent = '✗';
                row.querySelector('.hub-fit').title = data.error || t('hub_error');
                return;
            }
            const activeJob = { job_id: data.job_id, repo, file, module };
            rememberActiveDownload(activeJob);
            const ui = createDownloadRow(activeJob);
            // The "Download" button turns into a same-size red "Cancel" button
            // in place, so no extra control piles up on top of the file name.
            // A fresh <button> is used: the old node is disabled (browsers never
            // fire click on disabled buttons) and already carries the download
            // listener, which would re-submit the job on the next click.
            btn.replaceWith(ui.cancel);
            pollJob(data.job_id, ui.row, ui.cancel, repo, file, module);
        })
        .catch(() => { btn.disabled = false; row.querySelector('.hub-fit').textContent = '✗'; });
    }

    function restoreDownload(btn, repo, file, module) {
        const dlBtn = document.createElement('button');
        dlBtn.className = 'hub-dl add-user-button';
        dlBtn.dataset.repo = repo;
        dlBtn.dataset.file = file;
        dlBtn.dataset.module = module;
        dlBtn.textContent = t('hub_download');
        dlBtn.addEventListener('click', () => startDownload(dlBtn));
        if (btn.parentNode) btn.parentNode.replaceChild(dlBtn, btn);
    }

    function pollJob(jobId, cell, cancelBtn, repo, file, module) {
        const everyMs = 1000;
        cancelBtn.addEventListener('click', () => {
            fetchWithCSRF(`/admin/api/hub/cancel/${jobId}`, { method: 'POST' }).catch(() => {});
        });
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
                    if (job.state === 'starting' || job.state === 'downloading' || job.state === 'verifying') {
                        rememberActiveDownload({ job_id: jobId, repo, file, module });
                    }
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
                            .replace('{total}', fmtNum(Math.ceil(job.total_mb)));
                        setTimeout(tick, everyMs);
                    } else if (job.state === 'verifying') {
                        fill.style.width = '100%';
                        text.textContent = t('hub_verifying');
                        setTimeout(tick, everyMs);
                    } else if (job.state === 'done') {
                        fill.style.width = '100%';
                        cell.innerHTML = '✓ ' + esc(t('hub_done'));
                        if (typeof refreshModelsAfterHubDownload === 'function') {
                            refreshModelsAfterHubDownload();
                        }
                        forgetActiveDownload(jobId);
                        cancelBtn.remove();
                    } else if (job.state === 'cancelled') {
                        forgetActiveDownload(jobId);
                        cell.textContent = esc(t('hub_cancelled'));
                        restoreDownload(cancelBtn, repo, file, module);
                    } else if (job.state === 'failed') {
                        forgetActiveDownload(jobId);
                        cell.textContent = '✗ ' + esc(t('hub_failed')) + ': ' + esc(job.error || '');
                        restoreDownload(cancelBtn, repo, file, module);
                    }
                })
                .catch(() => { setTimeout(tick, everyMs); });
        })();
    }

    checkHubReachability();
    const hubTabBtn = document.querySelector('.admin-tab[data-tab="hub"]');
    if (hubTabBtn) hubTabBtn.addEventListener('click', () => checkHubReachability());
})();
