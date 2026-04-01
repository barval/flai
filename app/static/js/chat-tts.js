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
    ttsState = 'idle';
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
    setTTSButtonState(button, 'pending');
    updateSessionsListFromData();
    
    try {
        const response = await fetchWithCSRF('/api/tts/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text, lang: CURRENT_LANG })
        });
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