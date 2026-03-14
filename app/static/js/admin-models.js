// app/static/js/admin-models.js
// Handles model management tab in admin panel

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

let currentModelConfigs = {};

function loadModelConfigs() {
    fetch('/admin/api/model_configs')
        .then(res => res.json())
        .then(configs => {
            currentModelConfigs = configs;
            renderModelCards();
        })
        .catch(err => console.error('Error loading model configs:', err));
}

function refreshModels() {
    const btn = document.getElementById('refresh-models');
    btn.disabled = true;
    btn.textContent = '⏳ ' + (window.TRANSLATIONS['loading'] || 'Loading...');
    fetch('/admin/api/ollama/models')
        .then(res => res.json())
        .then(models => {
            // Store available models globally and re-render cards
            window.availableModels = models;
            renderModelCards();
        })
        .catch(err => {
            console.error('Error refreshing models:', err);
            alert(t('error') + ': ' + err.message);
        })
        .finally(() => {
            btn.disabled = false;
            btn.textContent = '🔄 ' + (window.TRANSLATIONS['Refresh models from Ollama'] || 'Refresh models from Ollama');
        });
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
        html += `
        <div class="model-card" data-module="${mod.id}">
            <h3><span class="module-name">${t(mod.name)}</span></h3>
            <div class="model-selector">
                <select class="model-dropdown" data-module="${mod.id}">
                    <option value="">${t('-- Select model --')}</option>
                </select>
            </div>
            <div class="model-details" id="details-${mod.id}" style="display:none;"></div>
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
                    <input type="number" class="timeout" data-module="${mod.id}" value="${mod.config.timeout || ''}" min="1" step="1">
                </div>
            </div>
            <button class="save-button" data-module="${mod.id}">${window.TRANSLATIONS['Save'] || 'Save'}</button>
        </div>
        `;
    });
    container.innerHTML = html;

    // Populate dropdowns with available models
    if (window.availableModels && window.availableModels.length) {
        const selects = document.querySelectorAll('.model-dropdown');
        selects.forEach(select => {
            const module = select.dataset.module;
            const currentModel = currentModelConfigs[module]?.model_name || '';
            window.availableModels.forEach(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                if (model === currentModel) option.selected = true;
                select.appendChild(option);
            });
        });
    }

    // Attach event listeners
    document.querySelectorAll('.model-dropdown').forEach(select => {
        select.addEventListener('change', onModelSelect);
    });
    document.querySelectorAll('.save-button').forEach(btn => {
        btn.addEventListener('click', onSaveConfig);
    });
}

function onModelSelect(event) {
    const select = event.target;
    const module = select.dataset.module;
    const modelName = select.value;
    if (!modelName) return;

    // Fetch model details
    fetch(`/admin/api/ollama/model/${encodeURIComponent(modelName)}`)
        .then(res => res.json())
        .then(info => {
            const detailsDiv = document.getElementById(`details-${module}`);
            detailsDiv.style.display = 'block';
            let caps = '';
            if (info.capabilities) {
                caps = '<div class="capabilities">';
                for (const [key, val] of Object.entries(info.capabilities)) {
                    caps += `<span class="capability ${val}">${key}</span>`;
                }
                caps += '</div>';
            }
            detailsDiv.innerHTML = `
                <p><strong>${t('Architecture:')}</strong> ${info.architecture || 'N/A'}</p>
                <p><strong>${t('Parameters:')}</strong> ${info.parameters || 'N/A'}</p>
                <p><strong>${t('Quantization:')}</strong> ${info.quantization || 'N/A'}</p>
                <p><strong>${t('Max context length:')}</strong> ${info.context_length || 'N/A'}</p>
                <p><strong>${t('Embedding length:')}</strong> ${info.embedding_length || 'N/A'}</p>
                ${caps}
            `;
            // Set max attribute for context length input
            const ctxInput = document.querySelector(`.context-length[data-module="${module}"]`);
            if (ctxInput && info.context_length) {
                ctxInput.max = info.context_length;
                if (!ctxInput.value) ctxInput.value = info.context_length;
            }
        })
        .catch(err => console.error('Error fetching model info:', err));
}

function onSaveConfig(event) {
    const btn = event.target;
    const module = btn.dataset.module;
    const card = document.querySelector(`.model-card[data-module="${module}"]`);

    const modelName = card.querySelector('.model-dropdown').value;
    const contextLength = card.querySelector('.context-length').value;
    const temperature = card.querySelector('.temperature').value;
    const topP = card.querySelector('.top-p').value;
    const timeout = card.querySelector('.timeout').value;

    const data = {
        model_name: modelName,
        context_length: parseInt(contextLength),
        temperature: parseFloat(temperature),
        top_p: parseFloat(topP),
        timeout: parseInt(timeout)
    };

    fetch(`/admin/api/model_configs/${module}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(result => {
        if (result.status === 'ok') {
            // Optionally show success
            btn.textContent = '✓ ' + (window.TRANSLATIONS['Saved'] || 'Saved');
            setTimeout(() => { btn.textContent = window.TRANSLATIONS['Save'] || 'Save'; }, 2000);
        } else {
            alert(t('error') + ': ' + (result.error || t('unknown_error')));
        }
    })
    .catch(err => {
        console.error('Error saving config:', err);
        alert(t('error') + ': ' + err.message);
    });
}