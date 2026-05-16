(function() {
    'use strict';

    const STYLE_ICONS = {
        neutral: '🤖',
        academic: '🎓',
        professional: '💼',
        friendly: '😊',
        funny: '😂',
    };

    function initResponseStyle() {
        const btn = document.getElementById('response-style-btn');
        const menu = document.getElementById('response-style-menu');
        if (!btn || !menu) return;

        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            menu.classList.toggle('show');
        });

        menu.querySelectorAll('.response-style-option').forEach(function(opt) {
            opt.addEventListener('click', function() {
                const style = this.dataset.style;
                if (!style) return;
                setResponseStyle(style, btn, menu);
            });
        });

        document.addEventListener('click', function() {
            menu.classList.remove('show');
        });

        menu.addEventListener('click', function(e) {
            e.stopPropagation();
        });
    }

    function setResponseStyle(style, btn, menu) {
        if (!STYLE_ICONS[style]) return;

        btn.textContent = STYLE_ICONS[style];
        btn.title = t('response_style_title');

        menu.querySelectorAll('.response-style-option').forEach(function(opt) {
            opt.classList.toggle('selected', opt.dataset.style === style);
        });
        menu.classList.remove('show');

        window.CURRENT_RESPONSE_STYLE = style;

        fetchWithCSRF('/set-response-style/' + style, { method: 'POST' }).catch(function() {
            window.location.reload();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initResponseStyle);
    } else {
        initResponseStyle();
    }
})();
