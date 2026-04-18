// app/static/js/admin-models.js
// Handles model management tab in admin panel
// Updated for llama.cpp (llama-server OpenAI-compatible API)

let currentModelConfigs = {};
let modelDetails = {};
let modelListCache = {};

function getCSRFToken() {
    const token = document.querySelector('meta[name="csrf-token"]');
    return token ? token.getAttribute('content') : '';
}

function fetchWithCSRF(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
        const headers = options.headers || {};
        if (!headers['X-CSRFToken'] && !headers['X-CSRF-TOKEN']) {
            headers['X-CSRFToken'] = getCSRFToken();
        }
        options.headers = headers;
    }
    return fetch(url, options);
}

function t(key) {
    if (!(key in window.TRANSLATIONS)) {
        console.warn('Missing translation key:', key);
        return key;
    }
    return window.TRANSLATIONS[key];
}

function initAdminTabs() {
    const tabs = document.querySelectorAll('.admin-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            const target = this.dataset.tab;
            document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            document.getElementById(target + '-tab').classList.add('active');
        });
    });
}

function loadModelConfigs() {
    fetch('/admin/api/model_configs')
        .then(res => res.json())
        .then(configs => {
            currentModelConfigs = configs;
            renderModelCards();
        })
        .catch(err => console.error('Error loading model configs:', err));
}

function renderModelCards() {
    const container = document.getElementById('models-list');
    if (!container) return;

    const modules = [
        { id: 'chat', name: 'Chat', config: currentModelConfigs.chat || {} },
        { id: 'reasoning', name: 'Reasoning', config: currentModelConfigs.reasoning || {} },
        { id: 'multimodal', name: 'Multimodal', config: currentModelConfigs.multimodal || {} },
        { id: 'embedding', name: 'Embedding', config: currentModelConfigs.embedding || {} }
    ];

    let html = '';
    modules.forEach(mod => {
        const serviceUrl = mod.config.service_url || mod.config.ollama_url || '';
        const isLocal = serviceUrl === 'http://llamacpp:8033' || serviceUrl === 'http://ollama:11434';

        html += `
        <div class="model-card" data-module="${mod.id}">
            <h3><span class="module-name">${t(mod.name)}</span></h3>
            <div class="model-url-group">
                <label class="checkbox-label">
                    <input type="checkbox" class="local-checkbox" data-module="${mod.id}" ${isLocal ? 'checked' : ''}>
                    ${t('Local')}
                </label>
                <div class="url-input-wrapper">
                    <input type="text" class="service-url" data-module="${mod.id}" value="${escapeHtml(serviceUrl)}" placeholder="http://llamacpp:8033">
                </div>
                <span class="service-status-icon" data-module="${mod.id}" title="">?</span>
            </div>
            <div class="model-selector">
                <button class="refresh-models-btn" data-module="${mod.id}" title="${t('Refresh models from llama-server')}">🔄</button>
                <select class="model-dropdown" data-module="${mod.id}">
                    <option value="">${t('-- Select model --')}</option>
                </select>
            </div>
            <div class="model-details" id="details-${mod.id}" style="display:none;"></div>`;

        if (mod.id !== 'embedding' && mod.id !== 'reranker') {
            html += `
            <div class="parameters">
                <div class="param">
                    <label>${t('Context Length')}</label>
                    <input type="number" class="context-length" data-module="${mod.id}" value="${mod.config.context_length || ''}" min="1" step="1">
                </div>
                <div class="param">
                    <label>${t('Temperature')}</label>
                    <input type="number" class="temperature" data-module="${mod.id}" value="${mod.config.temperature || ''}" min="0" max="2" step="0.01">
                </div>
                <div class="param">
                    <label>${t('Top P')}</label>
                    <input type="number" class="top-p" data-module="${mod.id}" value="${mod.config.top_p || ''}" min="0" max="1" step="0.01">
                </div>
                <div class="param">
                    <label>${t('Timeout (s)')}</label>
                    <input type="number" class="timeout" data-module="${mod.id}" value="${mod.config.timeout || ''}" min="0" max="1200" step="1">
                </div>
            </div>`;
        }

        html += `
            <button class="save-button" data-module="${mod.id}">${t('Save')}</button>
        </div>
        `;
    });
    container.innerHTML = html;

    document.querySelectorAll('.model-dropdown').forEach(select => {
        select.addEventListener('change', onModelSelect);
    });
    document.querySelectorAll('.refresh-models-btn').forEach(btn => {
        btn.addEventListener('click', onRefreshModels);
    });
    document.querySelectorAll('.save-button').forEach(btn => {
        btn.addEventListener('click', onSaveConfig);
    });
    document.querySelectorAll('.local-checkbox').forEach(cb => {
        cb.addEventListener('change', onLocalCheckboxChange);
    });
    document.querySelectorAll('.service-url').forEach(input => {
        input.addEventListener('input', function() {
            const module = this.dataset.module;
            updateServiceStatus(module);
        });
    });

    modules.forEach(mod => {
        updateServiceStatus(mod.id);
        const select = document.querySelector(`.model-dropdown[data-module="${mod.id}"]`);
        if (select && select.value) {
            onModelSelect({ target: select });
        }
    });
}

function onLocalCheckboxChange(event) {
    const cb = event.target;
    const module = cb.dataset.module;
    const urlInput = document.querySelector(`.service-url[data-module="${module}"]`);
    if (cb.checked) {
        urlInput.value = 'http://llamacpp:8033';
        urlInput.disabled = true;
        updateServiceStatus(module);
    } else {
        urlInput.disabled = false;
        updateServiceStatus(module);
    }
}

async function updateServiceStatus(module) {
    const urlInput = document.querySelector(`.service-url[data-module="${module}"]`);
    const serviceUrl = urlInput.value.trim();
    const statusIcon = document.querySelector(`.service-status-icon[data-module="${module}"]`);
    if (!serviceUrl) {
        statusIcon.textContent = '❓';
        statusIcon.title = t('Please provide llama-server URL first');
        return;
    }
    try {
        const response = await fetch(`/admin/api/llamacpp/check?url=${encodeURIComponent(serviceUrl)}`);
        const data = await response.json();
        if (data.available) {
            statusIcon.textContent = '✅';
            statusIcon.title = t('llama-server available');
        } else {
            statusIcon.textContent = '❌';
            statusIcon.title = t('llama-server unavailable') + (data.error ? `: ${data.error}` : '');
        }
    } catch (err) {
        statusIcon.textContent = '❌';
        statusIcon.title = t('llama-server unavailable') + ': ' + err.message;
    }
}

async function onRefreshModels(event) {
    const btn = event.target;
    const module = btn.dataset.module;
    btn.disabled = true;
    btn.textContent = '⏳';
    await refreshModelsForModule(module);
    btn.disabled = false;
    btn.textContent = '🔄';
}

async function refreshModelsForModule(module) {
    const urlInput = document.querySelector(`.service-url[data-module="${module}"]`);
    const serviceUrl = urlInput.value.trim();
    if (!serviceUrl) {
        showModelError(module, t('Please provide llama-server URL first'));
        return;
    }
    const select = document.querySelector(`.model-dropdown[data-module="${module}"]`);
    select.innerHTML = `<option value="">${t('-- Select model --')}</option>`;
    select.disabled = true;
    clearModelError(module);

    try {
        const response = await fetch(`/admin/api/llamacpp/models?url=${encodeURIComponent(serviceUrl)}`);
        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errorData = await response.json();
                if (errorData.error) errorMsg = errorData.error;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        const models = await response.json();
        modelListCache[serviceUrl] = models;
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            select.appendChild(option);
        });
    } catch (err) {
        showModelError(module, t('error') + ': ' + err.message);
    } finally {
        select.disabled = false;
        const currentConfig = currentModelConfigs[module] || {};
        if (currentConfig.model_name) {
            select.value = currentConfig.model_name;
        }
        if (select.value) {
            onModelSelect({ target: select });
        }
    }
}

function showModelError(module, message) {
    const card = document.querySelector(`.model-card[data-module="${module}"]`);
    let errorDiv = card.querySelector('.model-error');
    if (!errorDiv) {
        errorDiv = document.createElement('div');
        errorDiv.className = 'model-error';
        card.appendChild(errorDiv);
    }
    errorDiv.textContent = message;
}

function clearModelError(module) {
    const card = document.querySelector(`.model-card[data-module="${module}"]`);
    const errorDiv = card.querySelector('.model-error');
    if (errorDiv) errorDiv.remove();
}

async function onModelSelect(event) {
    const select = event.target;
    const module = select.dataset.module;
    const modelName = select.value;
    const detailsGrid = document.getElementById(`details-${module}`);
    if (!modelName) {
        detailsGrid.style.display = 'none';
        return;
    }
    detailsGrid.style.display = 'block';

    if (module === 'reranker') {
        const rerankerInfo = {
            architecture: 'BGE Cross-Encoder',
            parameters: '~560M',
            quantization: 'Q4_K_M',
            context_length: '8192'
        };
        detailsGrid.innerHTML = `
            <p><strong>${t('Architecture:')}</strong> ${rerankerInfo.architecture}</p>
            <p><strong>${t('Parameters:')}</strong> ${rerankerInfo.parameters}</p>
            <p><strong>${t('Quantization:')}</strong> ${rerankerInfo.quantization}</p>
            <p><strong>${t('Max context length:')}</strong> ${rerankerInfo.context_length}</p>
        `;
        return;
    }

    detailsGrid.innerHTML = '<p>Loading...</p>';
    clearModelError(module);

    const urlInput = document.querySelector(`.service-url[data-module="${module}"]`);
    const serviceUrl = urlInput.value.trim();
    if (!serviceUrl) {
        detailsGrid.innerHTML = '<p>No llama-server URL provided</p>';
        return;
    }

    let info = modelDetails[`${serviceUrl}:${modelName}`];
    if (!info) {
        try {
            const res = await fetch(`/admin/api/llamacpp/model/${encodeURIComponent(modelName)}?url=${encodeURIComponent(serviceUrl)}`);
            if (!res.ok) {
                let errorMsg = `HTTP ${res.status}`;
                try {
                    const errorData = await res.json();
                    if (errorData.error) errorMsg = errorData.error;
                } catch (e) {}
                throw new Error(errorMsg);
            }
            info = await res.json();
            modelDetails[`${serviceUrl}:${modelName}`] = info;
        } catch (err) {
            detailsGrid.innerHTML = `<p>${t('error')}: ${err.message}</p>`;
            return;
        }
    }

    let detailsHtml = `
        <p><strong>${t('Architecture:')}</strong> ${info.architecture || 'N/A'}</p>
        <p><strong>${t('Parameters:')}</strong> ${info.parameters || 'N/A'}</p>
        <p><strong>${t('Quantization:')}</strong> ${info.quantization || 'N/A'}</p>
    `;

    if (info.context_length && info.context_length !== 'N/A') {
        detailsHtml += `<p><strong>${t('Max context length:')}</strong> ${info.context_length}</p>`;
    }

    if (module === 'embedding' && info.embedding_length && info.embedding_length !== 'N/A') {
        detailsHtml += `<p><strong>${t('Embedding length:')}</strong> ${info.embedding_length}</p>`;
    }

    detailsGrid.innerHTML = detailsHtml;

    const ctxInput = document.querySelector(`.context-length[data-module="${module}"]`);
    if (ctxInput && info.context_length && info.context_length !== 'N/A') {
        ctxInput.max = info.context_length;
    }
}

function validateModelConfig(module, card) {
    const modelName = card.querySelector('.model-dropdown').value;
    if (!modelName) {
        alert(t('Please select a model first.'));
        return false;
    }

    const serviceUrl = card.querySelector('.service-url').value.trim();
    if (!serviceUrl) {
        alert(t('Please provide llama-server URL.'));
        return false;
    }

    if (module === 'embedding') return true;

    const contextLength = card.querySelector('.context-length')?.value;
    const temperature = card.querySelector('.temperature')?.value;
    const topP = card.querySelector('.top-p')?.value;
    const timeout = card.querySelector('.timeout')?.value;

    const info = modelDetails[`${serviceUrl}:${modelName}`];
    const maxContext = info && info.context_length && info.context_length !== 'N/A' ? parseInt(info.context_length) : null;

    if (contextLength !== undefined && contextLength !== '') {
        const val = parseInt(contextLength);
        if (isNaN(val) || val < 512) {
            alert(t('Context length must be at least 512.'));
            return false;
        }
        if (maxContext && val > maxContext) {
            alert(t('Context length cannot exceed {max} (max for this model).').replace('{max}', maxContext));
            return false;
        }
    }

    if (temperature !== undefined && temperature !== '') {
        const val = parseFloat(temperature);
        if (isNaN(val) || val < 0.0 || val > 2.0) {
            alert(t('Temperature must be between 0.0 and 2.0.'));
            return false;
        }
    }

    if (topP !== undefined && topP !== '') {
        const val = parseFloat(topP);
        if (isNaN(val) || val < 0.0 || val > 1.0) {
            alert(t('Top P must be between 0.0 and 1.0.'));
            return false;
        }
    }

    if (timeout !== undefined && timeout !== '') {
        const val = parseInt(timeout);
        if (isNaN(val) || val < 0 || val > 1200) {
            alert(t('Timeout must be between 0 and 1200 seconds.'));
            return false;
        }
    }

    return true;
}

function onSaveConfig(event) {
    const btn = event.target;
    const module = btn.dataset.module;
    const card = document.querySelector(`.model-card[data-module="${module}"]`);

    if (!validateModelConfig(module, card)) return;

    const modelName = card.querySelector('.model-dropdown').value;
    const serviceUrl = card.querySelector('.service-url').value.trim();
    const contextLength = card.querySelector('.context-length')?.value;
    const temperature = card.querySelector('.temperature')?.value;
    const topP = card.querySelector('.top-p')?.value;
    const timeout = card.querySelector('.timeout')?.value;

    const data = {
        model_name: modelName,
        service_url: serviceUrl,
        context_length: contextLength ? parseInt(contextLength) : null,
        temperature: temperature ? parseFloat(temperature) : null,
        top_p: topP ? parseFloat(topP) : null,
        timeout: timeout ? parseInt(timeout) : null
    };

    fetchWithCSRF(`/admin/api/model_configs/${module}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(result => {
        if (result.status === 'ok') {
            btn.textContent = '✓ ' + t('Saved');
            setTimeout(() => { btn.textContent = t('Save'); }, 2000);

            if (!currentModelConfigs[module]) currentModelConfigs[module] = {};
            Object.assign(currentModelConfigs[module], data);

            if (module === 'embedding') {
                window.CURRENT_EMBEDDING_MODEL = result.model_name;
                if (result.reindex_triggered) {
                    alert(t('reindex_started'));
                }
            }
        } else {
            alert(t('error') + ': ' + (result.error || t('unknown_error')));
        }
    })
    .catch(err => {
        console.error('Error saving config:', err);
        alert(t('error') + ': ' + err.message);
    });
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
}

function initChunksSection() {
    const saveChunksBtn = document.getElementById('save-chunks-btn');
    if (!saveChunksBtn) return;

    saveChunksBtn.addEventListener('click', async function() {
        const chunkSize = parseInt(document.getElementById('chunk-size').value) || 500;
        const chunkOverlap = parseInt(document.getElementById('chunk-overlap').value) || 50;
        const chunkStrategy = document.getElementById('chunk-strategy').value || 'fixed';

        const statusEl = document.getElementById('chunks-status');
        statusEl.textContent = '⏳';

        try {
            const response = await fetchWithCSRF('/admin/api/admin/chunks', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    chunk_size: chunkSize,
                    chunk_overlap: chunkOverlap,
                    chunk_strategy: chunkStrategy
                })
            });
            const result = await response.json();
            if (result.ok) {
                if (result.reindex_triggered) {
                    alert(t('chunks_saved'));
                } else {
                    alert(t('chunks_unchanged'));
                }
                statusEl.textContent = '✅';
            } else {
                alert(t('Error') + ': ' + (result.error || t('unknown_error')));
                statusEl.textContent = '❌';
            }
        } catch (err) {
            console.error('Save chunks error:', err);
            statusEl.textContent = '❌';
            alert(t('error'));
        }
    });

    const strategySelect = document.getElementById('chunk-strategy');
    if (strategySelect && typeof currentChunkStrategy !== 'undefined') {
        strategySelect.value = currentChunkStrategy;
    }
}

document.addEventListener('DOMContentLoaded', function() {
    initAdminTabs();
    if (document.getElementById('models-tab')) {
        loadModelConfigs();
    }
    initChunksSection();
});