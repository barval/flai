// app/static/js/chat-config.js
// Chat page configuration and translations injection
// This file is loaded after base-translations.js and provides chat-specific settings

(function() {
    // Session and user configuration
    window.initialSessionId = null; // Will be set by template
    window.CURRENT_USER_LOGIN = '';
    window.CURRENT_EMBEDDING_MODEL = '';

    // Chat-specific translations (merged with base translations)
    const chatTranslations = {
        'send': 'Send',
        'sending': 'Sending...',
        'recording': 'Recording...',
        'error_not_authorized': 'Not authorized',
        'empty_message': 'Empty message',
        'failed_recognize_speech': 'Failed to recognize speech',
        'voice_request': 'Voice request',
        'audio_transcribed': 'Audio transcribed',
        'request_queued': 'Request queued (position {pos})',
        'speech_recognized_queued': 'Speech recognized, request queued (position {pos})',
        'transcribed': 'Transcribed',
        'image_generated_from': 'Image generated from request: {query}',
        'camera_snapshot': 'Camera snapshot: {room_name}',
        'unknown_session': 'Unknown session',
        'new_session': 'New session',
        'delete_session': 'Delete session',
        'delete_session_confirm': 'Delete session {title} from {date}?',
        'permission_denied': 'Permission denied',
        'session_not_found': 'Session not found',
        'no_active_session': 'No active session',
        'save_as_html': 'Save as HTML',
        'chat': 'Chat',
        'sessions': 'Sessions',
        'documents': 'Documents',
        'messages': 'Messages',
        'your_requests': 'Your requests in queue / Total in queue',
        'error': 'Error',
        'warning': 'Warning',
        'info': 'Info',
        'user': 'user',
        'session': 'session',
        'saved_on': 'saved_on',
        'total_messages': 'total_messages',
        'speak': 'speak',
        'stop': 'stop',
        'download_image': 'download_image',
        'download_audio': 'download_audio',
        'click_to_enlarge': 'click_to_enlarge',
        'image': 'image',
        'enter_message_or_file': 'enter_message_or_file',
        'request_timeout': 'request_timeout',
        'unknown_error': 'unknown_error',
        'copied': 'copied',
        'copy_code': 'copy_code',
        'copy_failed': 'copy_failed',
        'copy_text': 'copy_text',
        'processing': 'processing',
        'queued': 'queued',
        'new_response': 'new_response',
        'browser_no_audio_support': 'browser_no_audio_support',
        'secure_context_required': 'secure_context_required',
        'microphone_access_denied': 'microphone_access_denied',
        'no_active_session_save': 'no_active_session_save',
        'saved_chat': 'saved_chat',
        'no_messages_to_save': 'no_messages_to_save',
        'footer_text': 'footer_text',
        'footer_not_configured': 'footer_not_configured',
        'footer_load_error': 'footer_load_error',
        'text_request': 'Text request',
        'record_voice_message': 'Record voice message',
        'seconds_suffix': 'seconds_suffix',
        'byte_abbr': 'byte_abbr',
        'kb_abbr': 'kb_abbr',
        'mb_abbr': 'mb_abbr',
        'gb_abbr': 'gb_abbr',
        'new_document': 'New document',
        'delete_document': 'Delete document',
        'delete_document_confirm': 'Delete document "{filename}"?',
        'upload_document': 'Upload document',
        'document_uploaded': 'Document uploaded',
        'document_upload_failed': 'Document upload failed',
        'supported_formats': 'Supported formats: PDF, DOC, DOCX, TXT, MD, ODT, RTF, CSV, JSON, EPUB',
        'minutes_abbr': 'minutes_abbr',
        'status_pending': 'status_pending',
        'status_indexing': 'status_indexing',
        'status_indexed': 'status_indexed',
        'status_failed': 'status_failed',
        'status_unknown': 'status_unknown',
        'transcribing': 'transcribing'
    };

    // Merge chat translations with base translations
    Object.assign(window.TRANSLATIONS, chatTranslations);

    // Translation helper function
    window.t = function(key) {
        if (!(key in window.TRANSLATIONS)) {
            console.warn('Missing translation key:', key);
            return key;
        }
        return window.TRANSLATIONS[key];
    };

    // Debug logging
    if (window.config && window.config.DEBUG_TRANSLATIONS) {
        console.log('Chat TRANSLATIONS merged:', window.TRANSLATIONS);
    }
})();
