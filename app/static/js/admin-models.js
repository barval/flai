// app/static/js/admin-models.js
// Handles model management tab in admin panel

let currentModelConfigs = {};
let modelDetails = {}; // cache for model info

document.addEventListener('DOMContentLoaded', function() {
    initAdminTabs();
    if (document.getElementById('models-tab')) {
        loadModelConfigs();
        document.getElementById('refresh-models').addEventListener('click', refreshModels);
    }
});

function initAdminTabs() {
    const tabs = document.querySelectorAll('.admin-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            const target = this.dataset.tab;
            // Deactivate all tabs
            document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.remove('active'));
            // Activate clicked tab
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
            // Immediately load models from Ollama
            refreshModels();
        })
        .catch(err => console.error('Error loading model configs:', err));
}

async function refreshModels() {
    const btn = document.getElementById('refresh-models');
    btn.disabled = true;
    btn.textContent = '⏳ ' + (t('loading') || 'Loading...');
    try {
        const models = await fetchModelsList();
        window.availableModels = models; // store names
        // Fetch details for all models
        await fetchAllModelsDetails(models);
        renderModelCards();
    } catch (err) {
        console.error('Error refreshing models:', err);
        alert(t('error') + ': ' + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '🔄 ' + (t('Refresh models from Ollama') || 'Refresh models from Ollama');
    }
}

async function fetchModelsList() {
    const res = await fetch('/admin/api/ollama/models');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
}

async function fetchAllModelsDetails(models) {
    const details = {};
    const concurrency = 5;
    // Split into chunks of `concurrency`
    for (let i = 0; i < models.length; i += concurrency) {
        const chunk = models.slice(i, i + concurrency);
        await Promise.all(chunk.map(async (model) => {
            try {
                const res = await fetch(`/admin/api/ollama/model/${encodeURIComponent(model)}`);
                if (res.ok) {
                    const info = await res.json();
                    details[model] = info;
                } else {
                    console.warn(`Failed to fetch details for ${model}`);
                }
            } catch (e) {
                console.warn(`Error fetching details for ${model}:`, e);
            }
        }));
    }
    modelDetails = details;
}

// Filtering helpers based on model details from Ollama
function isChatModel(modelName, info) {
    // Any model that is not an embedding model is suitable for chat
    return info && !info.is_embedding;
}

function isReasoningModel(modelName, info) {
    // For reasoning we also allow any non-embedding model (including multimodal)
    return info && !info.is_embedding;
}

function isMultimodalModel(modelName, info) {
    // Only models that explicitly support vision
    return info && info.is_vision;
}

function isEmbeddingModel(modelName, info) {
    // Only models that are identified as embedding models
    return info && info.is_embedding;
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

    const allModels = window.availableModels || [];

    let html = '';
    modules.forEach(mod => {
        // Filter models for this module
        let filteredModels = [];
        if (allModels.length) {
            filteredModels = allModels.filter(m => {
                const info = modelDetails[m];
                if (mod.id === 'chat') return isChatModel(m, info);
                if (mod.id === 'reasoning') return isReasoningModel(m, info);
                if (mod.id === 'multimodal') return isMultimodalModel(m, info);
                if (mod.id === 'embedding') return isEmbeddingModel(m, info);
                return true;
            });
        }

        html += `
        <div class="model-card" data-module="${mod.id}">
            <h3><span class="module-name">${t(mod.name)}</span></h3>
            <div class="model-selector">
                <select class="model-dropdown" data-module="${mod.id}">
                    <option value="">${t('-- Select model --')}</option>
                    ${filteredModels.map(m => {
                        const selected = (mod.config.model_name === m) ? 'selected' : '';
                        return `<option value="${m}" ${selected}>${m}</option>`;
                    }).join('')}
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
    document.querySelectorAll('.save-button').forEach(btn => {
        btn.addEventListener('click', onSaveConfig);
    });

    // After rendering, trigger info display for already selected models
    document.querySelectorAll('.model-card').forEach(card => {
        const module = card.dataset.module;
        const select = card.querySelector('.model-dropdown');
        if (select.value) {
            // Simulate change event to load details
            const event = new Event('change', { bubbles: true });
            select.dispatchEvent(event);
        }
    });
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

    // Try to get info from cache first
    let info = modelDetails[modelName];
    if (!info) {
        try {
            const res = await fetch(`/admin/api/ollama/model/${encodeURIComponent(modelName)}`);
            if (res.ok) {
                info = await res.json();
                modelDetails[modelName] = info;
            } else {
                detailsDiv.innerHTML = '<p>Error loading model info</p>';
                return;
            }
        } catch (err) {
            detailsDiv.innerHTML = '<p>Error loading model info</p>';
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

// Validation function for model parameters
function validateModelConfig(module, card) {
    const modelName = card.querySelector('.model-dropdown').value;
    if (!modelName) {
        alert(t('Please select a model first.'));
        return false;
    }

    // Skip validation for embedding module (no parameters)
    if (module === 'embedding') return true;

    const contextLength = card.querySelector('.context-length')?.value;
    const temperature = card.querySelector('.temperature')?.value;
    const topP = card.querySelector('.top-p')?.value;
    const timeout = card.querySelector('.timeout')?.value;

    // Get max context length for the selected model
    const info = modelDetails[modelName];
    const maxContext = info && info.context_length ? parseInt(info.context_length) : null;

    // Validate context length
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

    // Validate temperature
    if (temperature !== undefined && temperature !== '') {
        const val = parseFloat(temperature);
        if (isNaN(val) || val < 0.0 || val > 2.0) {
            alert(t('Temperature must be between 0.0 and 2.0.'));
            return false;
        }
    }

    // Validate top_p
    if (topP !== undefined && topP !== '') {
        const val = parseFloat(topP);
        if (isNaN(val) || val < 0.0 || val > 1.0) {
            alert(t('Top P must be between 0.0 and 1.0.'));
            return false;
        }
    }

    // Validate timeout
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

    // Run client-side validation
    if (!validateModelConfig(module, card)) {
        return;
    }

    const modelName = card.querySelector('.model-dropdown').value;
    const contextLength = card.querySelector('.context-length')?.value;
    const temperature = card.querySelector('.temperature')?.value;
    const topP = card.querySelector('.top-p')?.value;
    const timeout = card.querySelector('.timeout')?.value;

    const data = {
        model_name: modelName,
        context_length: contextLength ? parseInt(contextLength) : null,
        temperature: temperature ? parseFloat(temperature) : null,
        top_p: topP ? parseFloat(topP) : null,
        timeout: timeout ? parseInt(timeout) : null
    };

    fetch(`/admin/api/model_configs/${module}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(result => {
        console.log('Server response:', result); // Debug: log server response
        if (result.status === 'ok') {
            btn.textContent = '✓ ' + t('Saved');
            setTimeout(() => { btn.textContent = t('Save'); }, 2000);
            
            // If embedding module, update global variable and notify user about reindexing
            if (module === 'embedding') {
                // Update the global CURRENT_EMBEDDING_MODEL with the new model name
                if (result.model_name) {
                    window.CURRENT_EMBEDDING_MODEL = result.model_name;
                    console.log('Updated CURRENT_EMBEDDING_MODEL to', result.model_name);
                } else {
                    console.warn('No model_name in response, CURRENT_EMBEDDING_MODEL not updated');
                }
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