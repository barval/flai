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
    // The node waiting to be merged into another one (two clicks, no modal).
    let mergeSourceId = null;
    // The graph as the server sent it; the renderer only ever gets a filtered
    // view of it, so hiding a relation type never costs another request.
    let baseGraph = { nodes: [], links: [] };
    let lastTotalNodes = 0;

    const HIDDEN_PREDICATES_KEY = 'flai.graph.hiddenPredicates';
    const FILTER_DEFAULTS_KEY = 'flai.graph.filterDefaults';
    // The default is a version, not a value: a stored list from an older view
    // must still gain the new default once, and a reader who switches a type
    // back on keeps it on.
    const FILTER_DEFAULTS_VERSION = '2';
    // Structured noise: every pair of entities in one chunk is linked, which is
    // what pulled isolation from 43 % to 18 %. It is not a stated relation, so
    // the first view shows what the model actually claimed and this is one
    // click away.
    const HIDDEN_BY_DEFAULT = ['co-occurs'];

    // The extractor writes both language forms of its closed predicate list, so
    // these are data values, not identifiers.
    const PREDICATE_LABEL_KEYS = {
        'co-occurs': 'graph_pred_co_occurs',
        'similar': 'graph_pred_similar',
        'supplies': 'graph_pred_supplies',
        'поставляет': 'graph_pred_supplies',
        'belongs to': 'graph_pred_part_of',
        'входит в': 'graph_pred_part_of',
        'works for': 'graph_pred_works_for',
        'работает в': 'graph_pred_works_for',
        'leads': 'graph_pred_leads',
        'руководит': 'graph_pred_leads',
        'signed': 'graph_pred_signed',
        'подписал': 'graph_pred_signed',
        'rents': 'graph_pred_rents',
        'арендует': 'graph_pred_rents',
        'maintains': 'graph_pred_maintains',
        'обслуживает': 'graph_pred_maintains',
        'performs': 'graph_pred_performs',
        'выполняет': 'graph_pred_performs',
        'forbids': 'graph_pred_forbids',
        'запрещает': 'graph_pred_forbids',
        'allows': 'graph_pred_allows',
        'разрешает': 'graph_pred_allows',
        'located in': 'graph_pred_located_in',
        'расположен в': 'graph_pred_located_in',
        'related to': 'graph_pred_related_to',
        'связан с': 'graph_pred_related_to'
    };

    function predicateLabel(predicate) {
        const key = PREDICATE_LABEL_KEYS[predicate];
        return key ? t(key, predicate) : predicate;
    }
    // Read once at start-up, before any draw: the loader reads the key above,
    // and a const declared after this line would be in its temporal dead zone.
    let hiddenPredicates = new Set(loadHiddenPredicates());
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
        // A page that already carries the bundle (the published demo inlines it)
        // must not fetch a URL that cannot resolve from disk. In the served app
        // this is the same no-op: the global is defined once and stays.
        const globalName = kind === '3d' ? 'ForceGraph3D' : 'ForceGraph';
        if (typeof window[globalName] === 'function') return Promise.resolve();
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
            // The reason a 3D attempt failed stays on the button: the status line
            // is replaced by the next message, and "it opened in 2D again" without
            // the cause is a report nobody can act on.
            const reason = kind === '2d' && kindError
                ? ' · ' + t('graph_3d_unavailable', '3D is unavailable: {reason}').replace('{reason}', kindError)
                : '';
            button.title = (kind === '3d'
                ? t('graph_switch_to_2d', 'Switch to 2D')
                : t('graph_switch_to_3d', 'Switch to 3D')) + reason;
            button.disabled = false;
        }
        if (kind === '2d' && preferredKind() === '3d') {
            // Only say it when the fallback was not the user's choice.
            setStatus(t('graph_no_webgl', 'This browser does not provide WebGL — 2D mode'));
        }
    }

    function nodeColorFor(node) {
        return colorFor(node.kind, node);
    }

    // A link the extractor asserted (with a quote) and a link the graph adds
    // because two names share a chunk are different claims, so they never look
    // the same. Amber reads on white and on the dark background.
    const STRUCTURAL_PREDICATES = ['co-occurs'];

    function isStructural(predicate) {
        return STRUCTURAL_PREDICATES.indexOf(String(predicate || '')) !== -1;
    }

    function structuralLinkColor() {
        return document.body.classList.contains('dark-theme')
            ? 'rgba(240, 180, 91, 0.75)'
            : 'rgba(186, 118, 12, 0.6)';
    }

    function linkColorFor(link) {
        return link && isStructural(link.predicate) ? structuralLinkColor() : 'rgba(160,170,190,0.45)';
    }

    function linkWidthFor(link) {
        return link && isStructural(link.predicate)
            ? 0.8
            : 1.2 + Math.log(1 + ((link && link.weight) || 1)) * 1.6;
    }

    function linkDashFor(link) {
        return link && isStructural(link.predicate) ? [3, 3] : null;
    }

    async function createRenderer(kind, element, graph) {
        await loadVendor(kind);
        if (!element.clientHeight) {
            element.style.height = Math.max(320, (viewRoot() || {}).clientHeight || 480) + 'px';
        }
        let instance = buildRenderer(kind, element)
            .nodeLabel(function (node) { return escapeHtml(node.label); })
            .nodeColor(nodeColorFor)
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
            .linkColor(linkColorFor)
            .linkWidth(linkWidthFor)
            .onNodeClick(function (node) {
                if (mergeSourceId && mergeSourceId !== node.id) {
                    mergeInto(node);
                    return;
                }
                if (mergeSourceId === node.id) {
                    mergeSourceId = null;
                    setStatus(t('graph_merge_cancelled', 'Merge cancelled'));
                    selectedNode = node;
                    renderSidePanel(node);
                    return;
                }
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
        if (typeof instance.linkLineDash === 'function') {
            // Dashes say "this is not a stated relation" without reading the menu.
            instance.linkLineDash(linkDashFor);
        }
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

    function loadHiddenPredicates() {
        try {
            const stored = JSON.parse(window.localStorage.getItem(HIDDEN_PREDICATES_KEY) || '[]');
            const hidden = new Set(Array.isArray(stored) ? stored.map(String) : []);
            if (window.localStorage.getItem(FILTER_DEFAULTS_KEY) !== FILTER_DEFAULTS_VERSION) {
                HIDDEN_BY_DEFAULT.forEach(function (predicate) { hidden.add(predicate); });
                window.localStorage.setItem(FILTER_DEFAULTS_KEY, FILTER_DEFAULTS_VERSION);
                window.localStorage.setItem(HIDDEN_PREDICATES_KEY, JSON.stringify([...hidden]));
            }
            return [...hidden];
        } catch (error) {
            return HIDDEN_BY_DEFAULT.slice();
        }
    }

    function saveHiddenPredicates() {
        try {
            window.localStorage.setItem(HIDDEN_PREDICATES_KEY, JSON.stringify([...hiddenPredicates]));
        } catch (error) {
            // A private-mode browser refuses storage; the filter still works for this visit.
        }
    }

    function filteredGraph() {
        const links = (baseGraph.links || [])
            .filter(function (link) { return !hiddenPredicates.has(String(link.predicate || '')); })
            .map(function (link) { return Object.assign({}, link); });
        return { nodes: baseGraph.nodes || [], links: links };
    }

    function updateCounts(graph) {
        const counts = t('graph_counts', 'shown {n} of {total} nodes · {m} links')
            .replace('{n}', graph.nodes.length)
            .replace('{total}', lastTotalNodes || graph.nodes.length)
            .replace('{m}', graph.links.length);
        setStatus(counts);
    }

    function applyFilterToView() {
        if (!graphInstance) return;
        const graph = filteredGraph();
        graphInstance.graphData(graph);
        // A highlighted path overrides the base colours; a filter change is a
        // fresh view of the same data, so the highlight goes with it.
        resetColors();
        updateCounts(graph);
        fitGraph(300);
    }

    function renderFilters() {
        const list = document.getElementById('graph-filter-list');
        if (!list) return;
        const counts = new Map();
        (baseGraph.links || []).forEach(function (link) {
            const key = String(link.predicate || '');
            counts.set(key, (counts.get(key) || 0) + 1);
        });

        function rowFor(predicate, count) {
            const shown = hiddenPredicates.has(predicate) ? '' : ' checked';
            const dot = isStructural(predicate)
                ? '<span class="graph-filter-dot graph-filter-dot-structural"></span>'
                : '';
            return '<label class="graph-filter-row"><input type="checkbox" class="graph-filter-check" value="' +
                escapeHtml(predicate) + '"' + shown + '>' + dot + '<span>' +
                escapeHtml(predicateLabel(predicate) || '—') + '</span> <em>' + count + '</em></label>';
        }

        // Two groups, not one list: a link a model asserted with a quote and a
        // link that only says "these names share a chunk" are different claims,
        // and the count alone does not show which is which.
        const entries = [...counts.entries()].sort(function (a, b) { return b[1] - a[1]; });
        const stated = entries.filter(function (entry) { return !isStructural(entry[0]); });
        const structural = entries.filter(function (entry) { return isStructural(entry[0]); });

        function group(titleKey, titleFallback, items) {
            if (!items.length) return '';
            return '<div class="graph-filter-section"><div class="graph-filter-title">' +
                escapeHtml(t(titleKey, titleFallback)) + '</div>' +
                items.map(function (entry) { return rowFor(entry[0], entry[1]); }).join('') +
                '</div>';
        }

        list.innerHTML =
            group('graph_relations_stated', 'Stated relations', stated) +
            group('graph_relations_structural', 'Structural, no quote', structural) ||
            '<p class="graph-hint">' + escapeHtml(t('graph_no_relations', 'No relations')) + '</p>';

        list.querySelectorAll('.graph-filter-check').forEach(function (box) {
            box.addEventListener('change', function () {
                if (box.checked) hiddenPredicates.delete(box.value);
                else hiddenPredicates.add(box.value);
                saveHiddenPredicates();
                applyFilterToView();
            });
        });
    }

    function resetColors() {
        if (!graphInstance) return;
        graphInstance.nodeColor(nodeColorFor);
        graphInstance.linkColor(linkColorFor);
        const clear = document.getElementById('graph-clear-path');
        if (clear) clear.hidden = true;
    }

    function mergeButtonHTML(node) {
        if (!node || node.shared) return '';
        const label = mergeSourceId === node.id
            ? t('graph_merge_cancel', 'Cancel merge')
            : t('graph_merge_start', 'Merge into another node…');
        return '<button type="button" class="graph-merge-btn" id="graph-merge-start">' + escapeHtml(label) + '</button>';
    }

    function startMerge(node) {
        if (!node || node.shared) return;
        mergeSourceId = node.id;
        setStatus(t('graph_merge_pick', 'Pick the node it should be merged into'));
        renderSidePanel(node);
    }

    async function mergeInto(target) {
        const source = mergeSourceId;
        mergeSourceId = null;
        const sourceLabel = (selectedNode && selectedNode.label) || source;
        const question = t('graph_merge_confirm', 'Merge “{source}” into “{target}”? The first node disappears.')
            .replace('{source}', sourceLabel)
            .replace('{target}', (target && target.label) || '');
        if (!window.confirm(question)) {
            draw({ doc: focusedDoc, layer: currentLayer });
            return;
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        const headers = { 'Content-Type': 'application/json' };
        if (meta) headers['X-CSRFToken'] = meta.content;
        try {
            const response = await fetch('/api/graph/merge', {
                method: 'POST',
                credentials: 'same-origin',
                headers: headers,
                body: JSON.stringify({ source: source, target: target.id })
            });
            if (!response.ok) {
                setStatus(t('graph_merge_failed', 'The nodes could not be merged'));
                return;
            }
        } catch (error) {
            setStatus(t('graph_merge_failed', 'The nodes could not be merged'));
            return;
        }
        await draw({ doc: focusedDoc, layer: currentLayer });
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
                const cls = isStructural(link.predicate) ? ' class="graph-pred-structural"' : '';
                return '<li>' + escapeHtml(other.label || other.id || '') + ' — <em' + cls + '>' + escapeHtml(predicateLabel(link.predicate)) + '</em></li>';
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
            '<p class="graph-mentions">' + t('graph_mentions', 'Mentions') + ': ' + (node.mentions || 0) + '</p>' +
            mergeButtonHTML(node);
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
        mergeSourceId = null;
        renderSidePanel(null);

        // The renderers read `links`; the API names the array `edges`. The
        // server answer is kept whole: filtering is a view, not a request.
        baseGraph = {
            nodes: data.nodes,
            links: (data.edges || []).map(function (link) { return Object.assign({}, link); })
        };
        lastTotalNodes = data.total_nodes || data.nodes.length;
        renderFilters();
        const graph = filteredGraph();
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
            .replace('{n}', graph.nodes.length)
            .replace('{total}', lastTotalNodes || graph.nodes.length)
            .replace('{m}', graph.links.length);
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
        const clear = document.getElementById('graph-clear-path');
        if (clear) clear.hidden = false;
        setStatus(data.edges.map(function (edge) { return predicateLabel(edge.predicate); }).join(' → '));
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
        if (event.target.closest('#graph-merge-start')) {
            startMerge(selectedNode);
            return;
        }
        if (event.target.closest('#graph-clear-path')) {
            resetColors();
            setStatus(t('graph_path_cleared', 'Path highlight cleared'));
            return;
        }
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
