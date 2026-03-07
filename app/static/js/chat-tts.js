// static/js/chat-tts.js
// Text-to-speech functions

function setTTSButtonState(button, isPlaying) {
    if (isPlaying) {
        button.innerHTML = '🗣️';
        button.title = t('stop');
        button.classList.add('playing');
    } else {
        button.innerHTML = '🗣️';
        button.title = t('speak');
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
        setTTSButtonState(currentTTSButton, false);
        currentTTSButton = null;
    }
    currentPlayingSessionId = null;
    updateSessionsListFromData();
}

async function playTTS(button, messageElement) {
    const text = messageElement.dataset.rawText;
    if (!text) return;
    const sessionId = messageElement.dataset.sessionId;
    if (currentPlayingSessionId && currentPlayingSessionId !== sessionId) {
        resetTtsState();
    }
    if (currentAudio && currentTTSButton === button && !currentAudio.paused) {
        resetTtsState();
        return;
    }
    if (currentAudio) {
        resetTtsState();
    }
    try {
        const response = await fetch('/api/tts/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text, lang: CURRENT_LANG })
        });
        if (!response.ok) {
            const error = await response.json();
            alert(t('error') + ': ' + (error.error || t('unknown_error')));
            return;
        }
        const audioBlob = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        currentAudio = audio;
        currentTTSButton = button;
        currentPlayingSessionId = sessionId;
        setTTSButtonState(button, true);
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