// app/static/js/chat-graph.js
// Knowledge-graph view (v13.2): a 3D cloud of entities or documents that can be
// rotated, zoomed and explored. The renderer is vendored, loaded only when the
// view opens, and never fetched from a CDN.

(function () {
    'use strict';

    const VENDOR_SRC = '/static/vendor/3d-force-graph.min.js';
    let graphInstance = null;
    let vendorPromise = null;
    let currentLayer = 'entities';
    let focusedDoc = '';
    let selectedNode = null;
    let pathSourceId = null;

    function t(key, fallback) {
        return (window.TRANSLATIONS && window.TRANSLATIONS[key]) || fallback;
    }

    function loadVendor() {
        if (vendorPromise) return vendorPromise;
        vendorPromise = new Promise(function (resolve, reject) {
            const script = document.createElement('script');
            script.src = VENDOR_SRC;
            script.onload = resolve;
            script.onerror = function () {
                // A transient failure must not poison the view until a reload.
                vendorPromise = null;
                reject(new Error('renderer unavailable'));
            };
            document.head.appendChild(script);
        });
        return vendorPromise;
    }

    function viewRoot() {
        return document.getElementById('graph-view');
    }

    function apiBase(params) {
        const query = new URLSearchParams();
        if (params.doc) query.set('doc', params.doc);
        if (params.layer) query.set('layer', params.layer);
        if (params.kind) query.set('kind', params.kind);
        query.set('limit', String(params.limit || 300));
        return '/api/graph?' + query.toString();
    }

    async function fetchGraph(params) {
        const response = await fetch(apiBase(params), { credentials: 'same-origin' });
        if (!response.ok) throw new Error('graph request failed');
        return response.json();
    }

    function setStatus(text) {
        const status = document.getElementById('graph-status-line');
        if (status) status.textContent = text || '';
    }

    function renderSidePanel(node) {
        const panel = document.getElementById('graph-side-panel');
        if (!panel) return;
        if (!node) {
            panel.innerHTML = '<p class="graph-hint">' + t('graph_hint_select', 'Click a node to inspect it') + '</p>';
            return;
        }
        const neighbours = (graphInstance && graphInstance.graphData().links || [])
            .filter(function (link) {
                const source = typeof link.source === 'object' ? link.source.id : link.source;
                const target = typeof link.target === 'object' ? link.target.id : link.target;
                return source === node.id || target === node.id;
            })
            .map(function (link) {
                const source = typeof link.source === 'object' ? link.source : { id: link.source };
                const target = typeof link.target === 'object' ? link.target : { id: link.target };
                const other = source.id === node.id ? target : source;
                return '<li>' + escapeHtml(other.label || other.id || '') + ' — <em>' + escapeHtml(link.predicate || '') + '</em></li>';
            })
            .join('');
        panel.innerHTML =
            '<h4>' + escapeHtml(node.label) + '</h4>' +
            '<div class="graph-node-kind">' + escapeHtml(node.kind || '') + '</div>' +
            (node.description ? '<p>' + escapeHtml(node.description) + '</p>' : '') +
            (neighbours ? '<ul class="graph-neighbours">' + neighbours + '</ul>' : '') +
            '<p class="graph-mentions">' + t('graph_mentions', 'Mentions') + ': ' + (node.mentions || 0) + '</p>';
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function focusNode(node) {
        if (!graphInstance || !node) return;
        graphInstance.nodeColor(function (candidate) {
            if (!selectedNode) return colorFor(candidate.kind);
            if (candidate.id === selectedNode.id) return '#ffffff';
            const linked = (graphInstance.graphData().links || []).some(function (link) {
                const source = typeof link.source === 'object' ? link.source.id : link.source;
                const target = typeof link.target === 'object' ? link.target.id : link.target;
                return (source === selectedNode.id && target === candidate.id) ||
                    (target === selectedNode.id && source === candidate.id);
            });
            return linked ? colorFor(candidate.kind) : 'rgba(120,120,120,0.15)';
        });
        graphInstance.cameraPosition({ z: 220 }, node, 900);
    }

    function graphBackground() {
        return document.body.classList.contains('dark-theme') ? '#1e1e1e' : '#ffffff';
    }

    function colorFor(kind) {
        return {
            person: '#f6b26b',
            organization: '#6fa8dc',
            project: '#93c47d',
            document: '#8e7cc3',
            event: '#e06666',
            place: '#76a5af',
            term: '#b4a7d6',
        }[kind] || '#9aa0a6';
    }

    function showFailure(error) {
        // A blank canvas tells the user nothing: the reason goes to the panel
        // and to the console, and the status line stays readable.
        const message = (error && error.message) || String(error);
        setStatus(t('graph_error', 'The graph could not be loaded'));
        const panel = document.getElementById('graph-side-panel');
        if (panel) {
            panel.innerHTML =
                '<p class="graph-hint">' + escapeHtml(t('graph_error', 'The graph could not be loaded')) + '</p>' +
                '<p class="graph-error-detail">' + escapeHtml(message) + '</p>';
        }
        if (window.console) console.error('graph view:', error);
    }

    async function showEmptyState() {
        setStatus(t('graph_empty', 'The graph is still being built for these documents'));
        const panel = document.getElementById('graph-side-panel');
        if (!panel) return;
        let status = null;
        try {
            const response = await fetch('/api/graph/status', { credentials: 'same-origin' });
            if (response.ok) status = await response.json();
        } catch (error) {
            status = null;
        }
        const progress = status && status.chunks_total
            ? ' ' + status.chunks_done + '/' + status.chunks_total
            : '';
        panel.innerHTML =
            '<p class="graph-hint">' + escapeHtml(t('graph_empty', 'The graph is still being built for these documents')) +
            escapeHtml(progress) + '</p>' +
            '<button type="button" class="graph-build-btn" id="graph-build-now">' +
            escapeHtml(t('graph_build_now', 'Build the graph')) + '</button>';
        const button = document.getElementById('graph-build-now');
        if (button) button.addEventListener('click', startBuild);
    }

    async function startBuild() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        setStatus(t('graph_building', 'Building...'));
        try {
            const response = await fetch('/api/graph/build', {
                method: 'POST',
                credentials: 'same-origin',
                headers: meta ? { 'X-CSRFToken': meta.content } : {},
            });
            setStatus(response.ok
                ? t('graph_build_queued', 'Queued; the graph fills while the instance is idle')
                : t('graph_error', 'The graph could not be loaded'));
        } catch (error) {
            setStatus(t('graph_error', 'The graph could not be loaded'));
        }
    }

    async function draw(params) {
        const view = viewRoot();
        const canvas = document.getElementById('graph-canvas');
        if (!view || !canvas) return;
        setStatus(t('graph_loading', 'Loading the graph...'));
        let data;
        try {
            data = await fetchGraph(params);
        } catch (error) {
            showFailure(error);
            return;
        }
        if (!data.nodes || !data.nodes.length) {
            await showEmptyState();
            return;
        }
        selectedNode = null;
        renderSidePanel(null);

        if (graphInstance) {
            graphInstance.graphData(data);
        } else {
            try {
                await loadVendor();
            } catch (error) {
                showFailure(error);
                return;
            }
            if (typeof window.ForceGraph3D !== 'function') {
                showFailure(new Error('ForceGraph3D is missing from the vendored bundle'));
                return;
            }
            // The canvas must have a size before the renderer measures it.
            if (!canvas.clientHeight) {
                canvas.style.height = Math.max(320, (viewRoot() || {}).clientHeight || 480) + 'px';
            }
            try {
                graphInstance = window.ForceGraph3D()(canvas)
                .backgroundColor(graphBackground())
                .nodeLabel(function (node) { return escapeHtml(node.label); })
                .nodeColor(function (node) { return colorFor(node.kind); })
                .nodeVal(function (node) { return 1 + Math.log(1 + (node.mentions || 0)); })
                .linkLabel(function (link) { return escapeHtml(link.predicate || ''); })
                .linkColor(function () { return 'rgba(160,170,190,0.45)'; })
                .linkWidth(function (link) { return Math.max(0.5, Math.log(1 + (link.weight || 1))); })
                .onNodeClick(function (node) {
                    if (pathSourceId && pathSourceId !== node.id) {
                        showPath(pathSourceId, node.id);
                        pathSourceId = null;
                    }
                    selectedNode = node;
                    focusNode(node);
                    renderSidePanel(node);
                })
                .cameraPosition({ z: 700 })
                .onEngineStop(function () {
                    if (graphInstance && !graphInstance.__fitted) {
                        graphInstance.__fitted = true;
                        graphInstance.zoomToFit(600, 60);
                    }
                })
                .onNodeRightClick(function (node) {
                    pathSourceId = node.id;
                    setStatus(t('graph_path_pick', 'Pick the second node of the path'));
                })
                .graphData(data);
            } catch (error) {
                graphInstance = null;
                showFailure(error);
                return;
            }
        }
        setStatus(
            t('graph_counts', 'shown {n} of {total} nodes · {m} links')
                .replace('{n}', data.nodes.length)
                .replace('{total}', data.total_nodes || data.nodes.length)
                .replace('{m}', (data.edges || []).length)
        );
        // The fit happens when the engine stops; a fresh graph is repositioned
        // each time, so the flag is cleared here.
        graphInstance.__fitted = false;
    }

    async function showPath(sourceId, targetId) {
        const response = await fetch('/api/graph/path?source=' + encodeURIComponent(sourceId) +
            '&target=' + encodeURIComponent(targetId), { credentials: 'same-origin' });
        if (!response.ok) return;
        const data = await response.json();
        if (!data.edges || !data.edges.length) {
            setStatus(t('graph_no_path', 'No relation path was found'));
            return;
        }
        const ids = new Set();
        data.edges.forEach(function (edge) { ids.add(edge.source); ids.add(edge.target); });
        graphInstance.nodeColor(function (node) {
            return ids.has(node.id) ? '#ffffff' : 'rgba(120,120,120,0.12)';
        });
        graphInstance.linkColor(function (link) {
            const source = typeof link.source === 'object' ? link.source.id : link.source;
            const target = typeof link.target === 'object' ? link.target.id : link.target;
            return ids.has(source) && ids.has(target) ? '#ffd166' : 'rgba(160,170,190,0.08)';
        });
        setStatus(data.edges.map(function (edge) { return edge.predicate; }).join(' → '));
    }

    function setLayer(layer) {
        currentLayer = layer;
        draw({ doc: focusedDoc, layer: layer });
        document.querySelectorAll('.graph-layer-btn').forEach(function (button) {
            button.classList.toggle('active', button.dataset.layer === layer);
        });
    }

    window.openGraphView = function (options) {
        const view = viewRoot();
        if (!view) return;
        focusedDoc = (options && options.docId) || '';
        view.classList.remove('hidden');
        const title = document.getElementById('graph-title-text');
        if (title) title.textContent = focusedDoc ? t('graph_focused', 'Knowledge graph of the document') : t('graph_title', 'Knowledge graph');
        draw({ doc: focusedDoc, layer: currentLayer });
    };

    window.closeGraphView = function () {
        const view = viewRoot();
        if (view) view.classList.add('hidden');
    };

    window.loadGraph = function (params) {
        draw(params || { doc: focusedDoc, layer: currentLayer });
    };

    document.addEventListener('DOMContentLoaded', function () {
        // The theme is a body class; the canvas has to be repainted, because its
        // colour is set on the renderer, not in CSS.
        new MutationObserver(function () {
            if (graphInstance) graphInstance.backgroundColor(graphBackground());
        }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
        const back = document.getElementById('graph-back-btn');
        if (back) back.addEventListener('click', window.closeGraphView);
        document.querySelectorAll('.graph-layer-btn').forEach(function (button) {
            button.addEventListener('click', function () { setLayer(button.dataset.layer); });
        });
        const search = document.getElementById('graph-search-input');
        if (search) {
            search.addEventListener('keydown', function (event) {
                if (event.key !== 'Enter' || !graphInstance) return;
                const needle = search.value.trim().toLowerCase();
                if (!needle) return;
                const found = (graphInstance.graphData().nodes || []).find(function (node) {
                    return (node.label || '').toLowerCase().indexOf(needle) !== -1;
                });
                if (found) {
                    selectedNode = found;
                    focusNode(found);
                    renderSidePanel(found);
                }
            });
        }
    });
})();
