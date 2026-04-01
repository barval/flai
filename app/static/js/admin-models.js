// app/static/js/admin-models.js
// Handles model management tab in admin panel

let currentModelConfigs = {};
let modelDetails = {};        // cache for model info
let modelListCache = {};      // cache for list of models per URL

// Get CSRF token from meta tag
function getCSRFToken() {
    const token = document.querySelector('meta[name="csrf-token"]');
    return token ? token.getAttribute('content') : '';
}

// Fetch wrapper with CSRF token for POST/PUT/DELETE requests
function fetchWithCSRF(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    
    // Add CSRF token for state-changing requests
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
        const headers = options.headers || {};
        if (!headers['X-CSRFToken'] && !headers['X-CSRF-TOKEN']) {
            headers['X-CSRFToken'] = getCSRFToken();
        }
        options.headers = headers;
    }
    
    return fetch(url, options);
}

document.addEventListener('DOMContentLoaded', function() {
    initAdminTabs();
    if (document.getElementById('models-tab')) {
        loadModelConfigs();
    }
});

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
        const ollamaUrl = mod.config.ollama_url || '';
        const isLocal = ollamaUrl === 'http://ollama:11434';   // detect default local

        html += `
        <div class="model-card" data-module="${mod.id}">
            <h3><span class="module-name">${t(mod.name)}</span></h3>
            <div class="model-url-group">
                <label class="checkbox-label">
                    <input type="checkbox" class="local-checkbox" data-module="${mod.id}" ${isLocal ? 'checked' : ''}>
                    ${t('Local')}
                </label>
                <div class="url-input-wrapper">
                    <input type="text" class="ollama-url" data-module="${mod.id}" value="${escapeHtml(ollamaUrl)}" placeholder="http://ollama:11434">
                </div>
                <span class="ollama-status-icon" data-module="${mod.id}" title="">?</span>
            </div>
            <div class="model-selector">
                <button class="refresh-models-btn" data-module="${mod.id}" title="${t('Refresh models from Ollama')}">🔄</button>
                <select class="model-dropdown" data-module="${mod.id}">
                    <option value="">${t('-- Select model --')}</option>
                </select>
            </div>
            <div class="model-details" id="details-${mod.id}" style="display:none;"></div>`;

        // Show parameters only for non-embedding modules
        if (mod.id !== 'embedding') {
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

    // Attach event listeners
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
    document.querySelectorAll('.ollama-url').forEach(input => {
        input.addEventListener('input', function() {
            const module = this.dataset.module;
            updateOllamaStatus(module);
        });
    });

    // Initial status check for each module
    modules.forEach(mod => {
        updateOllamaStatus(mod.id);
        const select = document.querySelector(`.model-dropdown[data-module="${mod.id}"]`);
        if (select && select.value) {
            onModelSelect({ target: select });
        }
    });
}

function onLocalCheckboxChange(event) {
    const cb = event.target;
    const module = cb.dataset.module;
    const urlInput = document.querySelector(`.ollama-url[data-module="${module}"]`);
    if (cb.checked) {
        urlInput.value = 'http://ollama:11434';
        urlInput.disabled = true;
        updateOllamaStatus(module);
    } else {
        urlInput.disabled = false;
        updateOllamaStatus(module);
    }
}

async function updateOllamaStatus(module) {
    const urlInput = document.querySelector(`.ollama-url[data-module="${module}"]`);
    const ollamaUrl = urlInput.value.trim();
    const statusIcon = document.querySelector(`.ollama-status-icon[data-module="${module}"]`);
    if (!ollamaUrl) {
        statusIcon.textContent = '❓';
        statusIcon.title = t('Please provide Ollama URL first');
        return;
    }
    try {
        const response = await fetch(`/admin/api/ollama/check?url=${encodeURIComponent(ollamaUrl)}`);
        const data = await response.json();
        if (data.available) {
            statusIcon.textContent = '✅';
            statusIcon.title = t('Ollama available');
        } else {
            statusIcon.textContent = '❌';
            statusIcon.title = t('Ollama unavailable') + (data.error ? `: ${data.error}` : '');
        }
    } catch (err) {
        console.error(`Failed to check Ollama status for ${module}:`, err);
        statusIcon.textContent = '❌';
        statusIcon.title = t('Ollama unavailable') + ': ' + err.message;
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
    const urlInput = document.querySelector(`.ollama-url[data-module="${module}"]`);
    const ollamaUrl = urlInput.value.trim();
    if (!ollamaUrl) {
        showModelError(module, t('Please provide Ollama URL first'));
        return;
    }
    const select = document.querySelector(`.model-dropdown[data-module="${module}"]`);
    // Clear current options except placeholder
    select.innerHTML = `<option value="">${t('-- Select model --')}</option>`;
    select.disabled = true;

    // Clear previous error message
    clearModelError(module);

    try {
        const response = await fetch(`/admin/api/ollama/models?url=${encodeURIComponent(ollamaUrl)}`);
        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}`;
            try {
                const errorData = await response.json();
                if (errorData.error) errorMsg = errorData.error;
            } catch (e) {}
            throw new Error(errorMsg);
        }
        const models = await response.json();
        modelListCache[ollamaUrl] = models;
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            select.appendChild(option);
        });
    } catch (err) {
        console.error(`Failed to fetch models for ${module}:`, err);
        showModelError(module, t('error') + ': ' + err.message);
    } finally {
        select.disabled = false;
        // Restore previously selected model if exists
        const currentConfig = currentModelConfigs[module] || {};
        if (currentConfig.model_name) {
            select.value = currentConfig.model_name;
        }
        // Trigger details load if a model is selected
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
    const detailsDiv = document.getElementById(`details-${module}`);
    if (!modelName) {
        detailsDiv.style.display = 'none';
        return;
    }
    detailsDiv.style.display = 'block';
    detailsDiv.innerHTML = '<p>Loading...</p>';
    clearModelError(module);

    const urlInput = document.querySelector(`.ollama-url[data-module="${module}"]`);
    const ollamaUrl = urlInput.value.trim();
    if (!ollamaUrl) {
        detailsDiv.innerHTML = '<p>No Ollama URL provided</p>';
        return;
    }

    let info = modelDetails[`${ollamaUrl}:${modelName}`];
    if (!info) {
        try {
            const res = await fetch(`/admin/api/ollama/model/${encodeURIComponent(modelName)}?url=${encodeURIComponent(ollamaUrl)}`);
            if (!res.ok) {
                let errorMsg = `HTTP ${res.status}`;
                try {
                    const errorData = await res.json();
                    if (errorData.error) errorMsg = errorData.error;
                } catch (e) {}
                throw new Error(errorMsg);
            }
            info = await res.json();
            modelDetails[`${ollamaUrl}:${modelName}`] = info;
        } catch (err) {
            console.error(`Error loading model info for ${modelName}:`, err);
            detailsDiv.innerHTML = `<p>${t('error')}: ${err.message}</p>`;
            return;
        }
    }

    detailsDiv.innerHTML = `
        <p><strong>${t('Architecture:')}</strong> ${info.architecture || 'N/A'}</p>
        <p><strong>${t('Parameters:')}</strong> ${info.parameters || 'N/A'}</p>
        <p><strong>${t('Quantization:')}</strong> ${info.quantization || 'N/A'}</p>
        <p><strong>${t('Max context length:')}</strong> ${info.context_length || 'N/A'}</p>
        <p><strong>${t('Embedding length:')}</strong> ${info.embedding_length || 'N/A'}</p>
    `;

    // Set max attribute for context length input
    const ctxInput = document.querySelector(`.context-length[data-module="${module}"]`);
    if (ctxInput && info.context_length) {
        ctxInput.max = info.context_length;
    }
}

function validateModelConfig(module, card) {
    const modelName = card.querySelector('.model-dropdown').value;
    if (!modelName) {
        alert(t('Please select a model first.'));
        return false;
    }

    const ollamaUrl = card.querySelector('.ollama-url').value.trim();
    if (!ollamaUrl) {
        alert(t('Please provide Ollama URL.'));
        return false;
    }

    if (module === 'embedding') return true;   // no parameters to validate

    const contextLength = card.querySelector('.context-length')?.value;
    const temperature = card.querySelector('.temperature')?.value;
    const topP = card.querySelector('.top-p')?.value;
    const timeout = card.querySelector('.timeout')?.value;

    const info = modelDetails[`${ollamaUrl}:${modelName}`];
    const maxContext = info && info.context_length ? parseInt(info.context_length) : null;

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
    const ollamaUrl = card.querySelector('.ollama-url').value.trim();
    const contextLength = card.querySelector('.context-length')?.value;
    const temperature = card.querySelector('.temperature')?.value;
    const topP = card.querySelector('.top-p')?.value;
    const timeout = card.querySelector('.timeout')?.value;

    const data = {
        model_name: modelName,
        ollama_url: ollamaUrl,
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

            // Update global config cache
            if (!currentModelConfigs[module]) currentModelConfigs[module] = {};
            Object.assign(currentModelConfigs[module], data);

            if (module === 'embedding') {
                window.CURRENT_EMBEDDING_MODEL = result.model_name;
                alert(t('reindex_started'));
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
    }).replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, function(c) {
        return c;
    });
}