// static/js/chat-tts.js
// Text-to-speech functions

// TTS state: 'idle', 'pending', 'playing'
let ttsState = 'idle';

function setTTSButtonState(button, state) {
    // state: 'idle', 'pending', 'playing'
    if (state === 'playing') {
        button.innerHTML = '🗣️';
        button.title = t('stop');
        button.classList.remove('pending');
        button.classList.add('playing');
    } else if (state === 'pending') {
        button.innerHTML = '🗣️';
        button.title = t('loading');
        button.classList.add('pending');
        button.classList.remove('playing');
    } else {
        button.innerHTML = '🗣️';
        button.title = t('speak');
        button.classList.remove('pending');
        button.classList.remove('playing');
    }
}

function resetTtsState() {
    if (currentAudio) {
        currentAudio.pause();
        currentAudio.currentTime = 0;
        URL.revokeObjectURL(currentAudio.src);
        currentAudio = null;
    }
    if (currentTTSButton) {
        setTTSButtonState(currentTTSButton, 'idle');
        currentTTSButton = null;
    }
    currentPlayingSessionId = null;
    currentTTSMessageText = null;  // Clear stored text
    ttsState = 'idle';
    
    // Force immediate update of sessions list to remove TTS icon
    if (typeof updateSessionsList === 'function') {
        const sessions = Object.keys(sessionsData).map(id => ({
            id: id,
            title: sessionsData[id].title,
            updated_at: sessionsData[id].updated_at,
            message_count: sessionsData[id].message_count,
            has_unread: newMessageIndicators[id] ? true : false,
            queue_info: sessionQueueInfo[id] || null
        }));
        updateSessionsList(sessions);
    }
}

// Restore TTS button state when switching back to a session
function restoreTTSButtonState() {
    if (!currentPlayingSessionId || ttsState === 'idle') return;
    
    const messages = document.querySelectorAll('.user-message, .assistant-message, .bot-message');
    
    // First, try to find message by stored text (most accurate)
    if (currentTTSMessageText) {
        for (const msg of messages) {
            if (msg.dataset.sessionId === currentPlayingSessionId) {
                const rawText = msg.dataset.rawText;
                if (rawText === currentTTSMessageText) {
                    const ttsButton = msg.querySelector('.tts-button');
                    if (ttsButton) {
                        currentTTSButton = ttsButton;
                        setTTSButtonState(ttsButton, ttsState);
                        updateSessionsListFromData();
                        return;
                    }
                }
            }
        }
    }
    
    // Fallback: find by playing/pending class
    for (const msg of messages) {
        if (msg.dataset.sessionId === currentPlayingSessionId) {
            const ttsButton = msg.querySelector('.tts-button');
            if (ttsButton && (ttsButton.classList.contains('playing') || ttsButton.classList.contains('pending'))) {
                currentTTSButton = ttsButton;
                setTTSButtonState(ttsButton, ttsState);
                updateSessionsListFromData();
                return;
            }
        }
    }
    
    // Last fallback: find last message in session
    for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i];
        if (msg.dataset.sessionId === currentPlayingSessionId) {
            const ttsButton = msg.querySelector('.tts-button');
            if (ttsButton) {
                currentTTSButton = ttsButton;
                setTTSButtonState(ttsButton, ttsState);
                updateSessionsListFromData();
                return;
            }
        }
    }
    
    // If still no button found, just update the session list
    updateSessionsListFromData();
}

async function playTTS(button, messageElement) {
    const text = messageElement.dataset.rawText;
    if (!text) return;
    const sessionId = messageElement.dataset.sessionId;

    // If already playing or pending, stop immediately
    if (ttsState === 'playing' || ttsState === 'pending') {
        resetTtsState();
        return;
    }

    // If different session is playing, reset first
    if (currentPlayingSessionId && currentPlayingSessionId !== sessionId) {
        resetTtsState();
    }

    // Set pending state immediately (yellow background)
    ttsState = 'pending';
    currentTTSButton = button;
    currentPlayingSessionId = sessionId;
    currentTTSMessageText = text;  // Store text to find message later
    setTTSButtonState(button, 'pending');

    // Start fetch immediately - don't wait for UI update
    const fetchPromise = fetchWithCSRF('/api/tts/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text, lang: CURRENT_LANG })
    });

    // Update UI asynchronously - don't block audio
    if (typeof updateSessionsList === 'function') {
        const sessions = Object.keys(sessionsData).map(id => ({
            id: id,
            title: sessionsData[id].title,
            updated_at: sessionsData[id].updated_at,
            message_count: sessionsData[id].message_count,
            has_unread: newMessageIndicators[id] ? true : false,
            queue_info: sessionQueueInfo[id] || null
        }));
        updateSessionsList(sessions);
    }

    try {
        const t0 = performance.now();
        const response = await fetchPromise;
        const fetchTime = performance.now() - t0;
        console.debug(`TTS fetch completed in ${fetchTime.toFixed(0)}ms`);
        if (!response.ok) {
            const error = await response.json();
            alert(t('error') + ': ' + (error.error || t('unknown_error')));
            resetTtsState();
            return;
        }
        const audioBlob = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        currentAudio = audio;

        // Set playing state (red background) when actual playback starts
        ttsState = 'playing';
        setTTSButtonState(button, 'playing');
        updateSessionsListFromData();

        audio.onended = () => {
            URL.revokeObjectURL(audioUrl);
            resetTtsState();
        };
        audio.onerror = () => {
            resetTtsState();
        };
        audio.play();
    } catch (err) {
        console.error('TTS error:', err);
        alert(t('error') + ': ' + err.message);
        resetTtsState();
    }
}