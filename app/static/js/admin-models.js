// app/static/js/admin-models.js
// Handles model management tab in admin panel
// Updated for llama.cpp (llama-server OpenAI-compatible API)

let currentModelConfigs = {};
let modelDetails = {};
let modelListCache = {};

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
    console.log('loadModelConfigs called');
    fetch('/admin/api/model_configs', { credentials: 'same-origin' })
        .then(res => {
            console.log('model_configs response status:', res.status);
            return res.json();
        })
        .then(configs => {
            console.log('Loaded model configs:', configs);
            currentModelConfigs = configs;
            renderModelCards();
        })
        .catch(err => console.error('Error loading model configs:', err));
}

function renderModelCards() {
    console.log('renderModelCards called, container:', document.getElementById('models-list'));
    const container = document.getElementById('models-list');
    if (!container) {
        console.log('No models-list container!');
        return;
    }

    const modules = [
        { id: 'chat', name: 'Chat', config: currentModelConfigs.chat || {} },
        { id: 'reasoning', name: 'Reasoning', config: currentModelConfigs.reasoning || {} },
        { id: 'multimodal', name: 'Multimodal', config: currentModelConfigs.multimodal || {} },
        { id: 'embedding', name: 'Embedding', config: currentModelConfigs.embedding || {} }
    ];

    let html = '';
    modules.forEach(mod => {
        const selectedModel = mod.config.model_name || '';
        const maxCtx = mod.config.max_context_length || '32768';
        
        html += `
        <div class="model-card" data-module="${mod.id}">
            <h3><span class="module-name">${t(mod.name)}</span></h3>
            <input type="hidden" class="service-url" data-module="${mod.id}" value="http://flai-llamacpp:8033">
            <div class="model-selector">
                <select class="model-dropdown" data-module="${mod.id}">
                    <option value="">${t('-- Select model --')}</option>
                </select>
            </div>
            <div class="model-details" id="details-${mod.id}"></div>`;

        if (mod.id !== 'embedding' && mod.id !== 'reranker') {
            html += `
            <div class="parameters">
                <div class="param">
                    <label>${t('Context Length')}</label>
                    <input type="number" class="context-length" data-module="${mod.id}" value="${mod.config.context_length || ''}" min="1" step="1">
                    <small class="param-hint ctx-hint-${mod.id}">&lt; </small>
                </div>
                <div class="param">
                    <label>${t('Temperature')}</label>
                    <input type="number" class="temperature" data-module="${mod.id}" value="${mod.config.temperature || ''}" min="0" max="2" step="0.01">
                    <small class="param-hint">0.1 ... 1.0</small>
                </div>
                <div class="param">
                    <label>${t('Top P')}</label>
                    <input type="number" class="top-p" data-module="${mod.id}" value="${mod.config.top_p || ''}" min="0" max="1" step="0.01">
                    <small class="param-hint">0.1 ... 1.0</small>
                </div>
                <div class="param">
                    <label>${t('Timeout (s)')}</label>
                    <input type="number" class="timeout" data-module="${mod.id}" value="${mod.config.timeout || ''}" min="0" max="1200" step="1">
                </div>
                <div class="param">
                    <label>${t('Repeat Penalty')}</label>
                    <input type="number" class="repeat-penalty" data-module="${mod.id}" value="${mod.config.repeat_penalty ?? (mod.id === 'reasoning' ? 1.15 : 1.1)}" min="1.0" max="2.0" step="0.05">
                    <small class="param-hint">1.0 ... 2.0</small>
                </div>
            </div>`;
        }

        html += `
            <button class="save-button" data-module="${mod.id}">${t('Save')}</button>
        </div>
        `;
    });
    container.innerHTML = html;
    console.log('Rendered', modules.length, 'model cards');

    // Setup event listeners
    document.querySelectorAll('.model-dropdown').forEach(select => {
        select.addEventListener('change', onModelSelect);
    });
    document.querySelectorAll('.context-length').forEach(input => {
        input.addEventListener('change', onContextLengthChange);
    });
    document.querySelectorAll('.save-button').forEach(btn => {
        btn.addEventListener('click', onSaveConfig);
    });

    // Load models for each module
    modules.forEach(mod => {
        refreshModelsForModule(mod.id, true);
    });
}

async function onContextLengthChange(event) {
    const input = event.target;
    const module = input.dataset.module;
    const ctxLength = input.value;

    const serviceUrl = typeof LLAMA_SWAP_URL !== 'undefined' ? LLAMA_SWAP_URL : 'http://flai-llamaswap:8080';
    const modelSelect = document.querySelector(`.model-dropdown[data-module="${module}"]`);
    const modelName = modelSelect.value;

    if (!modelName) return;

    const modelInfo = modelDetails[`${serviceUrl}:${modelName}`];
    if (modelInfo) {
        await updateMemoryEstimation(module, modelInfo, ctxLength);
    }
}

async function refreshModelsForModule(module, silent = false) {
    const urlInput = document.querySelector(`.service-url[data-module="${module}"]`);
    let serviceUrl = urlInput.value.trim();
    let backend = 'llamacpp';
    
    // Use llama-swap URL if configured
    if (typeof LLAMA_SWAP_URL !== 'undefined' && LLAMA_SWAP_URL) {
        serviceUrl = LLAMA_SWAP_URL;
        backend = 'llama-swap';
    } else if (!serviceUrl || serviceUrl === 'http://flai-llamacpp:8033') {
        serviceUrl = 'http://flai-llamaswap:8080';
        backend = 'llama-swap';
    }
    
    const select = document.querySelector(`.model-dropdown[data-module="${module}"]`);
    const currentConfig = currentModelConfigs[module] || {};
    const currentModelName = currentConfig.model_name || '';

    select.innerHTML = `<option value="">${t('-- Select model --')}</option>`;
    select.disabled = true;
    clearModelError(module);

    try {
        let models = [];
        
        // For llama-swap mode, first try to get actual GGUF files from models directory
        if (backend === 'llama-swap') {
            console.log('Trying to load GGUF files from models directory');
            try {
                const ggufRes = await fetch('/admin/api/llamacpp/models?list_type=gguf_files');
                console.log('GGUF response status:', ggufRes.status);
                if (ggufRes.ok) {
                    models = await ggufRes.json();
                    console.log('Loaded GGUF files:', models);
                } else {
                    console.warn('GGUF files endpoint failed with status:', ggufRes.status);
                }
            } catch (e) {
                console.warn('Could not load GGUF files:', e);
            }
        }
        
        // If no GGUF files or not llama-swap, get from llama-server API
        if (models.length === 0) {
            console.log('Loading models from llama-server API, backend:', backend);
            const response = await fetch(`/admin/api/llamacpp/models?url=${encodeURIComponent(serviceUrl)}&backend=${backend}`);
            if (!response.ok) {
                if (!silent) {
                    let errorMsg = `HTTP ${response.status}`;
                    try {
                        const errorData = await response.json();
                        if (errorData.error) errorMsg = errorData.error;
                    } catch (e) {}
                    showModelError(module, t('error') + ': ' + errorMsg);
                }
                return;
            }
            models = await response.json();
        }
        
        modelListCache[serviceUrl] = models;
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            select.appendChild(option);
        });
    } catch (err) {
        if (!silent) {
            showModelError(module, t('error') + ': ' + err.message);
        }
    } finally {
        select.disabled = false;
        
        // Try to match current model
        if (currentModelName && select.options.length > 0) {
            // Simple approach: check if any option contains the current model name
            // Start from index 1 to skip the placeholder option
            let found = false;
            for (let i = 1; i < select.options.length; i++) {
                const optVal = select.options[i].value;
                const currentNorm = currentModelName.replace(/\.gguf$/, '').toLowerCase();
                const optNorm = optVal.replace(/\.gguf$/, '').toLowerCase();
                
                // Check various matches
                if (optVal === currentModelName || currentModelName === optVal ||
                    optNorm === currentNorm ||
                    optVal.includes(currentModelName) || currentModelName.includes(optVal) ||
                    optNorm.includes(currentNorm) || currentNorm.includes(optNorm)) {
                    select.selectedIndex = i;
                    select.value = optVal;
                    found = true;
                    console.log('Selected model at index', i, 'value:', optVal, 'for:', currentModelName);
                    break;
                }
            }
            
            // Last resort: check with path normalization
            if (!found) {
                const currentBase = currentModelName.split('/').pop().replace(/\.gguf$/, '');
                for (let i = 0; i < select.options.length; i++) {
                    const optBase = select.options[i].value.split('/').pop().replace(/\.gguf$/, '');
                    if (optBase === currentBase) {
                        select.selectedIndex = i;
                        found = true;
                        break;
                    }
                }
            }
            
            console.log('Model matching:', currentModelName, '-> found:', found);
            
            // Trigger model info load  
            if (found && select.selectedIndex >= 0) {
                setTimeout(() => {
                    onModelSelect({ target: select });
                }, 100);
            }
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
    console.log('onModelSelect:', module, modelName);
    const detailsGrid = document.getElementById(`details-${module}`);
    if (!modelName) {
        detailsGrid.innerHTML = '';
        return;
    }

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

    detailsGrid.innerHTML = `<p>${t('Loading...')}</p>`;
    clearModelError(module);

    const urlInput = document.querySelector(`.service-url[data-module="${module}"]`);
    let serviceUrl = typeof LLAMA_SWAP_URL !== 'undefined' ? LLAMA_SWAP_URL : 'http://flai-llamaswap:8080';
    if (!serviceUrl) {
        detailsGrid.innerHTML = `<p>${t('No llama-server URL provided')}</p>`;
        return;
    }

    const cacheKey = modelName;
    let info = modelDetails[cacheKey];
    let detailsHtml = '';
    if (!info) {
        try {
            const ctrl = new AbortController();
            const tid = setTimeout(() => ctrl.abort(), 30000);
            const res = await fetch(`/admin/api/llamacpp/model/${encodeURIComponent(modelName)}`, {signal: ctrl.signal});
            clearTimeout(tid);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const text = await res.text();
            info = JSON.parse(text);
            modelDetails[cacheKey] = info;
        } catch (err) {
            const errMsg = err.name === 'AbortError' ? t('timeout_fetching_model_info') : (err.message || String(err));
            detailsGrid.innerHTML = `<p>${t('error')}: ${errMsg}</p>`;
            return;
        }
    }

    detailsHtml = `<p><strong>${t('Architecture:')}</strong> ${info.architecture || 'N/A'}</p>
        <p><strong>${t('Parameters:')}</strong> ${info.parameters || 'N/A'}</p>
        <p><strong>${t('Quantization:')}</strong> ${info.quantization || 'N/A'}</p>`;

    if (info.context_length && info.context_length !== 'N/A') {
        detailsHtml += `<p><strong>${t('Max context length:')}</strong> ${info.context_length}</p>`;
    }

    if (module === 'embedding' && info.embedding_length && info.embedding_length !== 'N/A') {
        detailsHtml += `<p><strong>${t('Embedding length:')}</strong> ${info.embedding_length}</p>`;
    }

    if (info.file_size_mb) {
        const sizeMB = Math.round(info.file_size_mb);
        detailsHtml += `<p><strong>${t('File size:')}</strong> ${sizeMB} ${t('MB')}</p>`;
    }

    if (info.model_type) {
        detailsHtml += `<p><strong>${t('Type:')}</strong> ${info.model_type}</p>`;
    }

    detailsGrid.innerHTML = detailsHtml;

    const ctxInput = document.querySelector(`.context-length[data-module="${module}"]`);
    const contextLength = ctxInput ? ctxInput.value : '';
    await updateMemoryEstimation(module, info, contextLength);

    if (ctxInput && info.context_length && info.context_length !== 'N/A') {
        ctxInput.max = info.context_length;
        ctxInput.placeholder = `max: ${info.context_length}`;
        const hintSpan = document.querySelector(`.ctx-hint-${module}`);
        if (hintSpan) {
            hintSpan.textContent = '< ' + String(parseInt(info.context_length) + 1);
        }
    }
}

async function updateMemoryEstimation(module, modelInfo, ctxLength) {
    if (module === 'embedding') return;

    const card = document.querySelector(`.model-card[data-module="${module}"]`);
    let existingHint = card.querySelector('.memory-hint');
    if (existingHint) existingHint.remove();

    const modelSizeMB = modelInfo.file_size_mb;
    console.log('updateMemoryEstimation:', {module, modelInfo, modelSizeMB});
    
    if (!modelSizeMB) {
        // Try to estimate from model name
        const modelName = modelInfo.model_name || modelInfo.model_path || '';
        if (modelName.includes('27B') || modelName.includes('-27b')) {
            modelSizeMB = 16000;
        } else if (modelName.includes('20B') || modelName.includes('-20b')) {
            modelSizeMB = 12000;
        } else if (modelName.includes('12B') || modelName.includes('-12b')) {
            modelSizeMB = 8000;
        } else if (modelName.includes('9B') || modelName.includes('-9b')) {
            modelSizeMB = 6000;
        } else if (modelName.includes('4B') || modelName.includes('-4b')) {
            modelSizeMB = 3000;
        }
        console.log('Estimated modelSizeMB from name:', modelSizeMB);
    }
    
    const blockCount = modelInfo.block_count;
    const maxContext = modelInfo.context_length;
    const requestedCtx = parseInt(ctxLength) || 8192;

    try {
        const hwRes = await fetch('/admin/api/hardware', { credentials: 'same-origin' });
        if (!hwRes.ok) {
            console.log('Memory estimation skipped: hardware API error', hwRes.status);
            return;
        }
        const hw = await hwRes.json();
        console.log('Hardware info:', hw, 'modelSizeMB:', modelSizeMB);
        const availableVRAM = hw.available_vram_mb || 0;
        const totalRAM = hw.total_ram_mb || 0;
        const availableRAM = hw.available_ram_mb || 0;

        const hintDiv = document.createElement('div');
        hintDiv.className = 'memory-hint';
        hintDiv.style.cssText = 'margin-top: 8px; padding: 8px; border-radius: 4px; font-size: 0.85em;';

        function getMultiplier(modelName, modelSizeMB) {
            const size = modelSizeMB || 0;
            const name = (modelName || '').toUpperCase();
            
            if (name.includes('MXFP4') || name.includes('IQ3') || name.includes('IQ4') || name.includes('Q5') || name.includes('Q6') || name.includes('Q8')) {
                return 1.0;
            }
            
            if (size < 5000) {
                return 1.3;
            } else if (size < 10000) {
                return 1.1;
            } else {
                return 1.0;
            }
        }

        const multiplier = getMultiplier(modelInfo.model_name, modelSizeMB);
        const estimatedVRAM = modelSizeMB ? modelSizeMB * multiplier : 0;

        console.log('Hardware info:', hw);
        console.log('Model info:', {module, modelInfo, modelSizeMB, multiplier: getMultiplier(modelInfo.model_name, modelSizeMB)});
        const hasGPU = hw.cuda_detected;
        const totalVRAM = hw.total_vram_mb || 0;

        if (hasGPU && totalVRAM > 0) {
            if (!modelSizeMB) {
                hintDiv.style.color = '#1CC8E3';
                hintDiv.style.fontWeight = 'bold';
                hintDiv.textContent = 'GPU: ' + totalVRAM + 'MB';
            } else {
                const vramPercent = Math.round((modelSizeMB * multiplier / totalVRAM) * 100);
                const vramUsed = Math.min(vramPercent, 100);
                console.log('VRAM calc:', {modelSizeMB, multiplier, totalVRAM, vramPercent});

                if (vramPercent <= 100) {
                    hintDiv.style.color = '#29A847';
                    hintDiv.style.fontWeight = 'bold';
                    const msg = t('vram_optimal_dynamic').replace('%1%', vramPercent);
                    hintDiv.textContent = msg;
                } else {
                    const offloadPercent = vramPercent - 100;
                    if (offloadPercent <= 20) {
                        hintDiv.style.color = '#FFD700';
                        hintDiv.style.fontWeight = 'bold';
                        const msg = t('vram_partial_high');
                        hintDiv.textContent = msg.replace('%2%', offloadPercent);
                    } else if (offloadPercent <= 40) {
                        hintDiv.style.color = '#fd7e14';
                        hintDiv.style.fontWeight = 'bold';
                        const msg = t('vram_partial_med');
                        hintDiv.textContent = msg.replace('%2%', offloadPercent);
                    } else {
                        hintDiv.style.color = '#E01F1F';
                        hintDiv.style.fontWeight = 'bold';
                        const msg = t('vram_partial_low');
                        hintDiv.textContent = msg.replace('%2%', offloadPercent);
                    }
                }
            }
        } else if (hasGPU) {
            const gpuLabel = hw.gpu_name || 'GPU';
            if (!modelSizeMB) {
                hintDiv.style.color = '#1CC8E3';
                hintDiv.style.fontWeight = 'bold';
                hintDiv.textContent = gpuLabel;
            } else {
                const ramPercent = totalRAM > 0 ? Math.round((modelSizeMB * multiplier / totalRAM) * 100) : 0;
                hintDiv.style.color = '#fd7e14';
                hintDiv.style.fontWeight = 'bold';
                hintDiv.textContent = gpuLabel + '. ' + t('ram_estimate').replace('%1%', ramPercent);
            }
        } else {
            const ramPercent = totalRAM > 0 ? Math.round((modelSizeMB * multiplier / totalRAM) * 100) : 0;
            if (ramPercent < 50) {
                hintDiv.style.color = '#fd7e14';
                hintDiv.style.fontWeight = 'bold';
                const msg = t('no_gpu_low');
                hintDiv.textContent = msg.replace('%1%', ramPercent);
            } else {
                hintDiv.style.color = '#E01F1F';
                hintDiv.style.fontWeight = 'bold';
                const msg = t('no_gpu_full');
                hintDiv.textContent = msg.replace('%1%', ramPercent);
            }
        }

        const saveBtn = card.querySelector('.save-button');
        card.insertBefore(hintDiv, saveBtn);

    } catch (err) {
        console.error('Error updating memory estimation:', err);
    }
}

function validateModelConfig(module, card) {
    const modelName = card.querySelector('.model-dropdown').value;
    if (!modelName) {
        alert(t('Please select a model first.'));
        return false;
    }

    if (module === 'embedding') return true;

    const contextLength = card.querySelector('.context-length')?.value;
    const temperature = card.querySelector('.temperature')?.value;
    const topP = card.querySelector('.top-p')?.value;
    const timeout = card.querySelector('.timeout')?.value;

    const serviceUrl = typeof LLAMA_SWAP_URL !== 'undefined' ? LLAMA_SWAP_URL : 'http://flai-llamaswap:8080';
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

    const repeatPenalty = card.querySelector('.repeat-penalty')?.value;
    if (repeatPenalty !== undefined && repeatPenalty !== '') {
        const val = parseFloat(repeatPenalty);
        if (isNaN(val) || val < 1.0 || val > 2.0) {
            alert(t('Repeat penalty must be between 1.0 and 2.0.'));
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

    const repeatPenalty = card.querySelector('.repeat-penalty')?.value;

    const data = {
        model_name: modelName,
        service_url: serviceUrl,
        context_length: contextLength ? parseInt(contextLength) : null,
        temperature: temperature ? parseFloat(temperature) : null,
        top_p: topP ? parseFloat(topP) : null,
        timeout: timeout ? parseInt(timeout) : null,
        repeat_penalty: repeatPenalty ? parseFloat(repeatPenalty) : (module === 'reasoning' ? 1.15 : 1.1),
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

            if (module === 'reasoning' && result.max_top_k) {
                updateChunksLimits(result.max_top_k);
            }

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
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function updateChunksLimits(maxTopK) {
    const ragTopKInput = document.getElementById('rag-top-k');
    const ragTopKInputContainer = ragTopKInput ? ragTopKInput.parentElement : null;
    if (ragTopKInput) {
        ragTopKInput.max = maxTopK;
    }
    if (ragTopKInputContainer) {
        const hintSmall = ragTopKInputContainer.querySelector('small');
        if (hintSmall) {
            hintSmall.textContent = '< ' + maxTopK;
        }
    }
}

function initChunksSection() {
    const saveChunksBtn = document.getElementById('save-chunks-btn');
    if (!saveChunksBtn) return;

    saveChunksBtn.addEventListener('click', async function() {
        const chunkSize = parseInt(document.getElementById('chunk-size').value) || 500;
        const chunkOverlap = parseInt(document.getElementById('chunk-overlap').value) || 50;
        const chunkStrategy = document.getElementById('chunk-strategy').value || 'fixed';
        const ragTopK = parseInt(document.getElementById('rag-top-k').value) || 20;
        const thresholdDefault = parseFloat(document.getElementById('rag-threshold-default').value) || 0.3;
        const thresholdReasoning = parseFloat(document.getElementById('rag-threshold-reasoning').value) || 0.3;

        const statusEl = document.getElementById('chunks-status');
        statusEl.textContent = '⏳';

        try {
            const response = await fetchWithCSRF('/admin/api/admin/chunks', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    chunk_size: chunkSize,
                    chunk_overlap: chunkOverlap,
                    chunk_strategy: chunkStrategy,
                    rag_top_k: ragTopK,
                    rag_threshold_default: thresholdDefault,
                    rag_threshold_reasoning: thresholdReasoning
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
    console.log('DOMContentLoaded, checking models-tab:', document.getElementById('models-tab'));
    initAdminTabs();
    if (document.getElementById('models-tab')) {
        loadModelConfigs();
    }
    initChunksSection();
});