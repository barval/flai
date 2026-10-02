// app/static/js/footer-about.js
// "About" dialog opened from the footer brand label.
(function () {
    'use strict';

    var modal = document.getElementById('footer-about-modal');
    var btn = document.getElementById('footer-about-btn');
    if (!modal || !btn) return;

    var previouslyFocused = null;

    function openModal() {
        previouslyFocused = document.activeElement;
        modal.hidden = false;
        var closeBtn = modal.querySelector('.about-modal-close');
        if (closeBtn) closeBtn.focus();
        document.addEventListener('keydown', onKeydown);
    }

    function closeModal() {
        modal.hidden = true;
        document.removeEventListener('keydown', onKeydown);
        if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
            previouslyFocused.focus();
        }
    }

    function onKeydown(e) {
        if (e.key === 'Escape') closeModal();
    }

    btn.addEventListener('click', openModal);
    modal.querySelectorAll('[data-close-about]').forEach(function (el) {
        el.addEventListener('click', closeModal);
    });
})();
