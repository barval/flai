// app/static/js/chat-graph.js
// Knowledge-graph view (v13.2): a 3D cloud of entities or documents that can be
// rotated, zoomed and explored. The renderer is vendored, loaded only when the
// view opens, and never fetched from a CDN.

(function () {
    'use strict';

    const VENDOR_3D = '/static/vendor/3d-force-graph.min.js';
    const VENDOR_2D = '/static/vendor/force-graph.min.js';
    let graphInstance = null;
    let vendorPromise = null;
    let rendererKind = '3d';
    let currentLayer = 'entities';
    let focusedDoc = '';
    let selectedNode = null;
    let pathSourceId = null;
    // Why the last 3D attempt ended in 2D, kept until a 3D view succeeds.
    let kindError = '';

    function t(key, fallback) {
        return (window.TRANSLATIONS && window.TRANSLATIONS[key]) || fallback;
    }

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            const script = document.createElement('script');
            script.src = src;
            script.onload = resolve;
            script.onerror = function () { reject(new Error('renderer unavailable')); };
            document.head.appendChild(script);
        });
    }

    // 3D needs WebGL; the 2D renderer draws on a plain canvas and works where
    // the browser refuses a GL context, which is a common corporate setting.
    function loadVendor(kind) {
        if (vendorPromise && kind === rendererKind) return vendorPromise;
        rendererKind = kind;
        const source = kind === '3d' ? VENDOR_3D : VENDOR_2D;
        vendorPromise = loadScript(source).catch(function (error) {
            // A transient failure must not poison the view until a reload.
            vendorPromise = null;
            throw error;
        });
        return vendorPromise;
    }

    function preferredKind() {
        return window.__graphKind === '2d' ? '2d' : '3d';
    }

    function disposeRenderer(canvas) {
        try {
            if (graphInstance && typeof graphInstance._destructor === 'function') graphInstance._destructor();
        } catch (error) {
            // the renderer is being replaced anyway
        }
        graphInstance = null;
        if (canvas) canvas.innerHTML = '';
    }

    let fitTimer = null;
    let pendingFit = false;

    function fitGraph(delay) {
        // One fit per data load: the engine reports a stop after every zoom and
        // drag, and refitting there undid the user's own scaling.
        if (!pendingFit) return;
        if (fitTimer) clearTimeout(fitTimer);
        fitTimer = setTimeout(function () {
            fitTimer = null;
            if (!pendingFit || !graphInstance || typeof graphInstance.zoomToFit !== 'function') return;
            pendingFit = false;
            // The renderer must know the real slot before it centres anything:
            // with a stale, wider width the cloud is drawn off-centre.
            resizeRenderer();
            // Padding keeps the outermost nodes off the edge of the window.
            graphInstance.zoomToFit(800, 60);
        }, delay || 0);
    }

    function positionOverlay() {
        const view = viewRoot();
        if (!view) return;
        const sidebar = document.querySelector('.sessions-sidebar');
        const tabs = document.querySelector('.sessions-header');
        const desktop = window.matchMedia('(min-width: 769px)').matches;

        // The site header (language, theme, logout) stays above the graph.
        const siteHeader = document.querySelector('header');
        const headerBottom = siteHeader ? Math.max(0, Math.round(siteHeader.getBoundingClientRect().bottom)) : 0;

        let top = headerBottom;
        let left = 0;
        let width = window.innerWidth;
        let height = Math.max(240, Math.round(window.innerHeight - top));

        if (desktop && sidebar) {
            // The tabs and the lists keep their column; the graph takes the rest.
            const rect = sidebar.getBoundingClientRect();
            left = Math.max(0, Math.round(rect.right));
            width = Math.max(320, Math.round(window.innerWidth - left));
        } else if (tabs) {
            // A phone shows the tab row on top and the active view below it; the
            // graph is one more view in that space, not a full-screen cover.
            const rect = tabs.getBoundingClientRect();
            top = Math.max(headerBottom, Math.round(rect.bottom));
            height = Math.max(240, Math.round(window.innerHeight - top));
        }

        view.style.top = top + 'px';
        view.style.left = left + 'px';
        view.style.width = width + 'px';
        view.style.height = height + 'px';
    }

    function resizeRenderer() {
        const canvas = document.getElementById('graph-canvas');
        if (!graphInstance || !canvas) return;
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        if (width > 0 && height > 0 && typeof graphInstance.width === 'function') {
            graphInstance.width(width).height(height);
        }
    }

    function reportKind(kind) {
        const button = document.getElementById('graph-kind-btn');
        if (button) {
            // The label names the action, not the current mode: in 3D it offers
            // 2D, and the other way round.
            button.textContent = kind === '3d' ? '2D' : '3D';
            button.title = kind === '3d'
                ? t('graph_switch_to_2d', 'Switch to 2D')
                : t('graph_switch_to_3d', 'Switch to 3D');
            button.disabled = false;
        }
        if (kind === '2d' && preferredKind() === '3d') {
            // Only say it when the fallback was not the user's choice.
            setStatus(t('graph_no_webgl', 'This browser does not provide WebGL — 2D mode'));
        }
    }

    async function createRenderer(kind, element, graph) {
        await loadVendor(kind);
        if (!element.clientHeight) {
            element.style.height = Math.max(320, (viewRoot() || {}).clientHeight || 480) + 'px';
        }
        let instance = buildRenderer(kind, element)
            .nodeLabel(function (node) { return escapeHtml(node.label); })
            .nodeColor(function (node) { return colorFor(node.kind, node); })
            .nodeVal(function (node) { return 1 + Math.log(1 + (node.mentions || 0)); })
            .linkLabel(function (link) {
                // Document links carry a similarity score, not a verb.
                if (link.predicate === 'similar') {
                    return escapeHtml(
                        t('graph_similarity', 'similarity {v}').replace('{v}', Number(link.weight || 0).toFixed(2))
                    );
                }
                return escapeHtml(link.predicate || '');
            })
            .linkColor(function () { return 'rgba(160,170,190,0.45)'; })
            .linkWidth(function (link) { return 1.2 + Math.log(1 + (link.weight || 1)) * 1.6; })
            .onNodeClick(function (node) {
                if (pathSourceId && pathSourceId !== node.id) {
                    showPath(pathSourceId, node.id);
                    pathSourceId = null;
                }
                selectedNode = node;
                focusNode(node);
                renderSidePanel(node);
            })
            .onEngineStop(function () { fitGraph(0); })
            .onNodeRightClick(function (node) {
                pathSourceId = node.id;
                setStatus(t('graph_path_pick', 'Pick the second node of the path'));
            });
        if (kind === '3d' && typeof instance.backgroundColor === 'function') {
            instance.backgroundColor(graphBackground());
        }
        instance = instance.graphData(graph);
        if (kind === '3d' && typeof instance.cameraPosition === 'function') {
            instance.cameraPosition({ z: 700 });
        }
        resizeRenderer();
        return instance;
    }

    function buildRenderer(kind, element) {
        if (kind === '3d' && typeof window.ForceGraph3D === 'function') {
            return window.ForceGraph3D()(element);
        }
        if (typeof window.ForceGraph !== 'function') {
            throw new Error('ForceGraph is missing from the vendored bundle');
        }
        return window.ForceGraph()(element);
    }

    function viewRoot() {
        return document.getElementById('graph-view');
    }

    function apiBase(params) {
        const query = new URLSearchParams();
        if (params.doc) query.set('doc', params.doc);
        if (params.layer) query.set('layer', params.layer);
        if (params.kind) query.set('kind', params.kind);
        // No limit from here: the server caps the scene with GRAPH_MAX_NODES, so
        // the admin setting is the one place that decides how much is drawn.
        if (params.limit) query.set('limit', String(params.limit));
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
            (node.shared && node.owner
                ? '<div class="graph-node-shared">👥 ' + escapeHtml(t('graph_shared_by', 'Shared by')) + ': ' +
                  escapeHtml(node.owner) + '</div>'
                : '') +
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
            if (!selectedNode) return colorFor(candidate.kind, candidate);
            if (candidate.id === selectedNode.id) return '#ffffff';
            const linked = (graphInstance.graphData().links || []).some(function (link) {
                const source = typeof link.source === 'object' ? link.source.id : link.source;
                const target = typeof link.target === 'object' ? link.target.id : link.target;
                return (source === selectedNode.id && target === candidate.id) ||
                    (target === selectedNode.id && source === candidate.id);
            });
            return linked ? colorFor(candidate.kind, candidate) : 'rgba(120,120,120,0.15)';
        });
        // The 2D renderer has no camera; it flies by centring instead.
        if (typeof graphInstance.cameraPosition === 'function') {
            graphInstance.cameraPosition({ z: 220 }, node, 900);
        } else if (typeof graphInstance.centerAt === 'function') {
            graphInstance.centerAt(node.x, node.y, 900);
            graphInstance.zoom(3, 900);
        }
    }

    function graphBackground() {
        return document.body.classList.contains('dark-theme') ? '#1e1e1e' : '#ffffff';
    }

    function colorFor(kind, node) {
        // A document somebody else shared reads as a different kind of thing:
        // it is visible, searchable, and not yours to rename or delete.
        if (node && node.shared) return '#e69138';
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
                '<p class="graph-error-detail">' + escapeHtml(message) + '</p>' +
                '<p class="graph-hint">' + escapeHtml(t('graph_error_hint', 'Open the browser console for details, or copy the line above.')) + '</p>';
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
        reportLayer((params && params.layer) || currentLayer);
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

        // The renderers read `links`; the API names the array `edges`.
        const graph = { nodes: data.nodes, links: data.edges || [] };
        let kind = preferredKind();
        reportKind(kind);
        if (graphInstance && rendererKind !== kind) {
            disposeRenderer(canvas);
        }
        if (graphInstance) {
            graphInstance.graphData(graph);
        } else {
            try {
                graphInstance = await createRenderer(kind, canvas, graph);
            } catch (error) {
                if (kind !== '3d') {
                    showFailure(error);
                    return;
                }
                // WebGL refused the context: fall back once, and say why. The
                // attempt is real — a pre-flight probe rejects browsers that
                // would have rendered fine.
                const reason3d = error;
                kindError = (reason3d && reason3d.message) || String(reason3d);
                disposeRenderer(canvas);
                try {
                    kind = '2d';
                    // Remembered for the session so the next open does not pay
                    // for the same refusal; the 3D button retries on demand.
                    window.__graphKind = '2d';
                    reportKind(kind);
                    graphInstance = await createRenderer('2d', canvas, graph);
                    const message = (reason3d && reason3d.message) || String(reason3d);
                    // Show what 3D actually said: calling every failure "no WebGL"
                    // hides a bug behind an environment explanation.
                    if (window.console) console.error('graph 3D failed:', reason3d);
                } catch (fallbackError) {
                    showFailure(fallbackError);
                    return;
                }
            }
        }
        if (kind === '3d') {
            kindError = '';
        }
        // The loading label has to be replaced by the actual size of the view.
        // The reason a 3D attempt fell back to 2D rides along: writing the
        // counts over it is why a browser without WebGL looked like a broken
        // button instead of an explained fallback.
        const counts = t('graph_counts', 'shown {n} of {total} nodes · {m} links')
            .replace('{n}', data.nodes.length)
            .replace('{total}', data.total_nodes || data.nodes.length)
            .replace('{m}', (data.edges || []).length);
        if (kind === '2d' && kindError) {
            const reason = /webgl|context/i.test(kindError)
                ? t('graph_no_webgl', 'This browser does not provide WebGL — 2D mode')
                : t('graph_3d_unavailable', '3D is unavailable: {reason}').replace('{reason}', kindError);
            setStatus(counts + ' · ' + reason);
        } else {
            setStatus(counts);
        }
        // The whole cloud has to be visible on open: the engine reports a stop
        // long before the layout settles, so the fit is repeated after it relaxes.
        pendingFit = true;
        fitGraph(1500);
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

    // The label names the action, not the current layer: while entities are
    // shown the button offers documents, and the other way round.
    function reportLayer(layer) {
        const button = document.getElementById('graph-layer-btn');
        if (!button) return;
        const other = layer === 'documents' ? 'entities' : 'documents';
        button.dataset.layer = other;
        button.textContent = other === 'documents'
            ? t('graph_layer_documents', 'Documents')
            : t('graph_layer_entities', 'Entities');
        button.title = other === 'documents'
            ? t('graph_switch_to_documents', 'Switch to documents')
            : t('graph_switch_to_entities', 'Switch to entities');
    }

    function setLayer(layer) {
        if (!layer) return;
        currentLayer = layer;
        reportLayer(layer);
        draw({ doc: focusedDoc, layer: layer });
    }

    window.openGraphView = function (options) {
        const view = viewRoot();
        if (!view) return;
        focusedDoc = (options && options.docId) || '';
        positionOverlay();
        view.classList.remove('hidden');
        const title = document.getElementById('graph-title-text');
        if (title) title.textContent = focusedDoc ? t('graph_focused', 'Knowledge graph of the document') : t('graph_title', 'Knowledge graph');
        draw({ doc: focusedDoc, layer: currentLayer });
    };

    window.closeGraphView = function () {
        const view = viewRoot();
        if (view) view.classList.add('hidden');
    };

    window.isGraphViewActive = function () {
        const view = viewRoot();
        return !!view && !view.classList.contains('hidden');
    };

    window.loadGraph = function (params) {
        draw(params || { doc: focusedDoc, layer: currentLayer });
    };

    // One document-level listener: it works on any layout and survives every
    // re-render of the documents list.
    document.addEventListener('click', function (event) {
        if (!event.target || !event.target.closest) return;
        const cardButton = event.target.closest('.graph-document-button');
        const allButton = event.target.closest('#documents-graph-button');
        if (!cardButton && !allButton) return;
        event.preventDefault();
        event.stopPropagation();
        if (typeof window.openGraphView === 'function') {
            window.openGraphView(cardButton ? { docId: cardButton.dataset.graphDoc } : {});
        }
    });

    document.addEventListener('DOMContentLoaded', function () {
        // The theme is a body class; the canvas has to be repainted, because its
        // colour is set on the renderer, not in CSS.
        new MutationObserver(function () {
            if (graphInstance) graphInstance.backgroundColor(graphBackground());
        }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
        const canvasBox = document.getElementById('graph-canvas');
        if (canvasBox) {
            ['wheel', 'pointerdown', 'touchstart'].forEach(function (eventName) {
                canvasBox.addEventListener(eventName, function () {
                    pendingFit = false;
                    if (fitTimer) {
                        clearTimeout(fitTimer);
                        fitTimer = null;
                    }
                }, { passive: true });
            });
        }
        if (canvasBox && typeof ResizeObserver === 'function') {
            new ResizeObserver(function () { resizeRenderer(); }).observe(canvasBox);
        }
        window.addEventListener('resize', function () { positionOverlay(); resizeRenderer(); });
        const kindButton = document.getElementById('graph-kind-btn');
        if (kindButton) {
            kindButton.addEventListener('click', function () {
                window.__graphKind = preferredKind() === '3d' ? '2d' : '3d';
                draw({ doc: focusedDoc, layer: currentLayer });
            });
        }
        // Only the layer buttons carry a layer; the 2D/3D switch shares the
        // class and used to call setLayer(undefined), which deselected both.
        const layerButton = document.getElementById('graph-layer-btn');
        if (layerButton) {
            layerButton.addEventListener('click', function () { setLayer(layerButton.dataset.layer); });
        }
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
