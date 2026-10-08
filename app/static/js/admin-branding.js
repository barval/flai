// app/static/js/admin-branding.js
// Personalization tab: custom header logo + localized site names.
(function () {
    'use strict';

    const logoPreview = document.getElementById('branding-logo-preview');
    const logoInput = document.getElementById('branding-logo-input');
    const uploadBtn = document.getElementById('branding-logo-upload-btn');
    const deleteBtn = document.getElementById('branding-logo-delete-btn');
    const namesForm = document.getElementById('branding-names-form');
    const nameRu = document.getElementById('branding-name-ru');
    const nameEn = document.getElementById('branding-name-en');
    if (!logoPreview || !uploadBtn) return;

    const t = (key) => (window.TRANSLATIONS && window.TRANSLATIONS[key]) || key;
    const DEFAULT_LOGO = logoPreview.src;

    function setDeleteVisibility(hasLogo) {
        deleteBtn.hidden = !hasLogo;
    }

    async function safeJson(resp) {
        const text = await resp.text();
        try {
            return JSON.parse(text);
        } catch (e) {
            throw new Error('HTTP ' + resp.status + ' ' + resp.statusText + (text ? ' — ' + text.slice(0, 120) : ''));
        }
    }

    async function loadBranding() {
        try {
            const resp = await fetch('/admin/api/branding', { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await safeJson(resp);
            logoPreview.src = data.logo_url || DEFAULT_LOGO;
            setDeleteVisibility(!!data.has_logo);
            nameRu.value = data.site_name_ru || '';
            nameEn.value = data.site_name_en || '';
        } catch (err) {
            console.error('Branding load failed:', err);
            alert(t('branding_load_error'));
        }
    }

    uploadBtn.addEventListener('click', () => logoInput.click());

    logoInput.addEventListener('change', async () => {
        const file = logoInput.files && logoInput.files[0];
        if (!file) return;
        if (file.size > 2 * 1024 * 1024) {
            alert(t('branding_logo_too_large'));
            logoInput.value = '';
            return;
        }
        const form = new FormData();
        form.append('logo', file);
        const csrf = namesForm.querySelector('input[name="csrf_token"]');
        uploadBtn.disabled = true;
        try {
            const resp = await fetch('/admin/api/branding/logo', {
                method: 'POST',
                credentials: 'same-origin',
                headers: csrf ? { 'X-CSRFToken': csrf.value } : undefined,
                body: form,
            });
            const data = await safeJson(resp);
            if (!resp.ok) throw new Error(data.error || t('branding_upload_failed'));
            logoPreview.src = data.logo_url + (data.logo_url.includes('?') ? '&' : '?') + '_r=' + Date.now();
            setDeleteVisibility(true);
            alert(t('branding_logo_uploaded'));
        } catch (err) {
            console.error('Logo upload failed:', err);
            alert(err.message || t('branding_upload_failed'));
        } finally {
            uploadBtn.disabled = false;
            logoInput.value = '';
        }
    });

    deleteBtn.addEventListener('click', async () => {
        const csrf = namesForm.querySelector('input[name="csrf_token"]');
        deleteBtn.disabled = true;
        try {
            const resp = await fetch('/admin/api/branding/logo', {
                method: 'DELETE',
                credentials: 'same-origin',
                headers: csrf ? { 'X-CSRFToken': csrf.value } : undefined,
            });
            const data = await safeJson(resp);
            if (!resp.ok) throw new Error(data.error || 'HTTP ' + resp.status);
            logoPreview.src = DEFAULT_LOGO + (DEFAULT_LOGO.includes('?') ? '&' : '?') + '_r=' + Date.now();
            setDeleteVisibility(false);
            alert(t('branding_logo_deleted'));
        } catch (err) {
            console.error('Logo delete failed:', err);
            alert(err.message || 'HTTP error');
        } finally {
            deleteBtn.disabled = false;
        }
    });

    namesForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const ru = nameRu.value.trim();
        const en = nameEn.value.trim();
        if (!ru && !en) {
            // Deliberate reset — both empty means "use the default brand name".
        } else if (!ru || !en) {
            alert(t('branding_both_required'));
            return;
        } else if (ru.length > 40 || en.length > 40) {
            alert(t('branding_name_too_long'));
            return;
        }
        const csrf = namesForm.querySelector('input[name="csrf_token"]');
        try {
            const resp = await fetch('/admin/api/branding/names', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    ...(csrf ? { 'X-CSRFToken': csrf.value } : {}),
                },
                body: JSON.stringify({ site_name_ru: ru, site_name_en: en }),
            });
            const data = await safeJson(resp);
            if (!resp.ok) throw new Error(data.error || 'HTTP ' + resp.status);
            nameRu.value = data.site_name_ru || '';
            nameEn.value = data.site_name_en || '';
            alert(t('branding_names_saved'));
            // The header shows the names for the *current* session language only
            // after reload — reload is the simplest way to see the change.
            window.location.reload();
        } catch (err) {
            console.error('Names save failed:', err);
            alert(err.message || 'HTTP error');
        }
    });

    loadBranding();
})();
