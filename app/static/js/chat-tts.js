// static/js/chat-tts.js
// Text-to-speech with sentence-level chunking for low-latency streaming

// TTS state: 'idle', 'pending', 'playing'
let ttsState = 'idle';

// Abort controller for cancelling pending synthesis
let ttsAbortController = null;
// Queue of audio URLs ready for playback (filled in order)
let ttsAudioQueue = [];
// Indexed buffer to maintain correct sentence order
let ttsAudioBuffer = [];
// How many entries have been drained from the front of the buffer
let ttsDrainOffset = 0;
let ttsIsGenerating = false;  // Background synthesis in progress

// These are already declared in chat-constants.js — reuse them
// currentAudio, currentTTSButton, currentPlayingSessionId, currentTTSMessageText

function setTTSButtonState(button, state) {
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

/**
 * Split text into sentences for chunked TTS synthesis.
 * Splits on sentence boundaries (.!? followed by space or end of string).
 * Preserves abbreviations like "г.", "т.д." by using negative lookahead.
 */
function splitIntoSentences(text) {
    // Common abbreviations that shouldn't split on
    const abbreviations = '(?:г|т|тд|и|тп|тд|напр|пр|ул|пр-кт|бул|пер|кв|ж|д|корп|стр|оф|тел|тел|факс|e-mail|www|http|https|Mr|Mrs|Ms|Dr|Prof|Jr|Sr|vs|etc|No|Vol|pp|fig|Fig|eq|Eq|approx|cf|e\.g|i\.e|vs|al|№)';

    // Match sentence endings
    const sentenceRegex = new RegExp(
        `[^.!?]*(?:${abbreviations}\\.)+[^.!?]*[.!?]+\\s*|[^.!?]+[.!?]+\\s*|.+`,
        'g'
    );

    const sentences = text.match(sentenceRegex) || [text];
    return sentences
        .map(s => s.trim())
        .filter(s => s.length > 0);
}

function resetTtsState() {
    // Abort any pending fetch requests
    if (ttsAbortController) {
        ttsAbortController.abort();
        ttsAbortController = null;
    }

    // Stop current audio
    if (currentAudio) {
        currentAudio.pause();
        currentAudio.currentTime = 0;
        if (currentAudio.src && currentAudio.src.startsWith('blob:')) {
            URL.revokeObjectURL(currentAudio.src);
        }
        currentAudio = null;
    }

    // Revoke queued and buffered audio URLs
    [...ttsAudioQueue, ...ttsAudioBuffer.filter(Boolean)].forEach(url => {
        if (url) URL.revokeObjectURL(url);
    });
    ttsAudioQueue = [];
    ttsAudioBuffer = [];
    ttsDrainOffset = 0;
    ttsCurrentAudio = null;
    ttsIsGenerating = false;

    if (currentTTSButton) {
        setTTSButtonState(currentTTSButton, 'idle');
        currentTTSButton = null;
    }
    currentPlayingSessionId = null;
    currentTTSMessageText = null;
    ttsState = 'idle';

    updateSessionsListFromData();
}

/**
 * Background synthesizer: pre-synthesize sentences while audio is playing.
 * Places audio at the correct index in the buffer, then drains completed
 * entries to the playback queue in order.
 */
async function synthesizeInBackground(sentences, lang, startIndex) {
    if (ttsIsGenerating) return;
    ttsIsGenerating = true;

    for (let i = startIndex; i < sentences.length; i++) {
        // Check if cancelled
        if (!ttsAbortController || ttsAbortController.signal.aborted) {
            ttsIsGenerating = false;
            return;
        }

        try {
            let response = await fetchWithCSRF('/api/tts/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: sentences[i],
                    lang: lang
                }),
                signal: ttsAbortController.signal
            });

            if (!response.ok) {
                dwarn('TTS background chunk HTTP error:', response.status);
                await new Promise(r => setTimeout(r, 3000));
                response = await fetchWithCSRF('/api/tts/synthesize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text: sentences[i],
                        lang: lang
                    }),
                    signal: ttsAbortController.signal
                });
                if (!response.ok) {
                    dwarn('TTS background chunk retry also failed:', response.status);
                    continue;
                }
            }

            const audioBlob = await response.blob();
            const audioUrl = URL.createObjectURL(audioBlob);

            // Place at correct index (accounting for already-drained entries)
            const bufIdx = i - ttsDrainOffset;
            if (bufIdx >= 0) {
                ttsAudioBuffer[bufIdx] = audioUrl;
            }

            // Drain completed entries from the front of the buffer to the queue
            drainBufferToQueue();

            // If nothing is playing and we have audio, start playback
            if (!currentAudio && ttsState === 'pending') {
                playNextFromQueue();
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                ttsIsGenerating = false;
                return;
            }
            dwarn('TTS background chunk error:', err);
        }
    }

    ttsIsGenerating = false;
}

/**
 * Move completed (non-null, non-undefined) entries from the front of the
 * buffer to the playback queue, maintaining correct sentence order.
 * Only drains contiguous entries starting from index 0 of the logical buffer.
 */
function drainBufferToQueue() {
    for (let i = 0; i < ttsAudioBuffer.length; i++) {
        const entry = ttsAudioBuffer[i];
        if (entry === undefined) {
            // Gap — not yet synthesized, stop here
            break;
        } else if (entry === null) {
            // Failed synthesis — mark as drained and skip
            ttsAudioBuffer[i] = '__drained__';
            ttsDrainOffset++;
        } else {
            // Synthesized audio — move to queue
            ttsAudioQueue.push(ttsAudioBuffer[i]);
            ttsAudioBuffer[i] = '__drained__';
            ttsDrainOffset++;
        }
    }
    // Compact buffer: remove drained entries from the front
    while (ttsAudioBuffer.length > 0 && ttsAudioBuffer[0] === '__drained__') {
        ttsAudioBuffer.shift();
    }
}

/**
 * Play the next audio URL from the queue.
 * If queue is empty but generation is still running, wait for it.
 */
function playNextFromQueue() {
    if (ttsAudioQueue.length === 0) {
        if (ttsIsGenerating) {
            // Wait a bit and try again
            setTimeout(() => playNextFromQueue(), 200);
        } else {
            // All done
            resetTtsState();
        }
        return;
    }

    if (!ttsAbortController || ttsAbortController.signal.aborted) return;

    const audioUrl = ttsAudioQueue.shift();
    const audio = new Audio(audioUrl);
    currentAudio = audio;
    ttsCurrentAudio = audio;

    // Update UI to playing state
    if (ttsState !== 'playing') {
        ttsState = 'playing';
        if (currentTTSButton) {
            setTTSButtonState(currentTTSButton, 'playing');
            updateSessionsListFromData();
        }
    }

    audio.onended = () => {
        URL.revokeObjectURL(audioUrl);
        currentAudio = null;
        ttsCurrentAudio = null;
        // Play next chunk
        playNextFromQueue();
    };

    audio.onerror = () => {
        dwarn('TTS chunk playback error');
        currentAudio = null;
        ttsCurrentAudio = null;
        URL.revokeObjectURL(audioUrl);
        playNextFromQueue();
    };

    audio.play().catch(err => {
        dwarn('TTS play error:', err);
        currentAudio = null;
        ttsCurrentAudio = null;
    });
}

// Restore TTS button state when switching back to a session
function restoreTTSButtonState() {
    if (!currentPlayingSessionId || ttsState === 'idle') return;

    const messages = document.querySelectorAll('.user-message, .assistant-message, .bot-message');

    if (currentTTSMessageText) {
        for (const msg of messages) {
            if (msg.dataset.sessionId === currentPlayingSessionId) {
                const rawText = msg.dataset.cleanText || msg.dataset.rawText;
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

    updateSessionsListFromData();
}

async function playTTS(button, messageElement) {
    try {
        const text = messageElement.dataset.cleanText || messageElement.dataset.rawText;
        if (!text) {
            dwarn('playTTS: no rawText found');
            return;
        }
        const sessionId = messageElement.dataset.sessionId;

        dlog('playTTS called, text length:', text.length);

        // If already playing or pending, stop immediately
        if (ttsState === 'playing' || ttsState === 'pending') {
            dlog('playTTS: stopping current playback');
            resetTtsState();
            return;
        }

        // If different session is playing, reset first
        if (currentPlayingSessionId && currentPlayingSessionId !== sessionId) {
            resetTtsState();
        }

        // Set pending state immediately
        ttsState = 'pending';
        currentTTSButton = button;
        currentPlayingSessionId = sessionId;
        currentTTSMessageText = text;
        setTTSButtonState(button, 'pending');
        ttsAudioQueue = [];
        ttsAudioBuffer = [];
        ttsDrainOffset = 0;

        // Create abort controller for this TTS session
        ttsAbortController = new AbortController();

        // Update UI
        if (typeof updateSessionsListFromData === 'function') {
            updateSessionsListFromData();
        }

        // Split text into sentences
        const sentences = splitIntoSentences(text);
        dlog('playTTS: split into', sentences.length, 'sentences');

        // Initialize buffer with correct size
        ttsAudioBuffer = new Array(sentences.length);

        const lang = undefined;  // Let server use session.get('language')

        // Synthesize first sentence and play it IMMEDIATELY — don't wait for the second.
        // This eliminates the 15-20s delay caused by sequential torch inference.
        let firstAudioUrl = null;
        let firstIdx = 0;
        for (let i = 0; i < sentences.length; i++) {
            try {
                const requestBody = { text: sentences[i] };
                if (lang) requestBody.lang = lang;
                const response = await fetchWithCSRF('/api/tts/synthesize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody),
                    signal: ttsAbortController.signal
                });
                if (response.ok) {
                    const audioBlob = await response.blob();
                    firstAudioUrl = URL.createObjectURL(audioBlob);
                    firstIdx = i;
                    break;
                }
                dwarn('TTS first chunk HTTP error:', response.status);
            } catch (err) {
                if (err.name === 'AbortError') return;
                dwarn('TTS first chunk error:', err);
            }
        }

        if (ttsState !== 'pending' || !firstAudioUrl) {
            dwarn('playTTS: cancelled or no audio generated');
            resetTtsState();
            return;
        }

        // Play first sentence immediately
        dlog('playTTS: playing first sentence immediately');

        // The first sentence plays directly (outside the buffer). Mark it as
        // already-drained so background synthesis at bufIdx = i - ttsDrainOffset
        // aligns to index 0 and drainBufferToQueue() doesn't hit a gap.
        for (let k = 0; k <= firstIdx; k++) {
            ttsAudioBuffer[k] = '__drained__';
        }
        ttsDrainOffset = firstIdx + 1;

        // Start background synthesis for remaining sentences (including skipped ones)
        const bgStart = firstIdx + 1;
        if (bgStart < sentences.length) {
            dlog('playTTS: starting background synthesis from sentence', bgStart);
            synthesizeInBackground(sentences, lang, bgStart);
        }

        // Play first audio directly — background will queue the rest
        const audio = new Audio(firstAudioUrl);
        currentAudio = audio;
        ttsCurrentAudio = audio;
        ttsState = 'playing';
        setTTSButtonState(button, 'playing');
        updateSessionsListFromData();

        audio.onended = () => {
            URL.revokeObjectURL(firstAudioUrl);
            currentAudio = null;
            ttsCurrentAudio = null;
            playNextFromQueue();
        };
        audio.onerror = () => {
            dwarn('TTS first chunk playback error');
            currentAudio = null;
            ttsCurrentAudio = null;
            URL.revokeObjectURL(firstAudioUrl);
            playNextFromQueue();
        };
        audio.play().catch(err => {
            dwarn('TTS play error:', err);
            currentAudio = null;
            ttsCurrentAudio = null;
        });
    } catch (err) {
        console.error('playTTS error:', err);
        resetTtsState();
    }
}
