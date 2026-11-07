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

// Current voice gender from the header toggle. Sent explicitly with every
// synthesize request: a stale session cookie (race right after
// /set-voice-gender) must never make playback use the previous gender.
// Returns undefined when the toggle is absent — the server then falls back
// to session["voice_gender"].
function getVoiceGender() {
    const btn = document.getElementById('voice-gender-toggle');
    if (!btn) return undefined;
    return btn.classList.contains('female') ? 'female' : 'male';
}

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

// Russian forms whose dot never ends a sentence: the abbreviation continues the
// token (the "that is" / "so forth" pair). Deliberately short — ICU's
// segmentation knows the rest, and the long list this replaces missed two of
// these while carrying "tel" and "vs" twice each.
const NON_TERMINAL_ABBREVIATIONS = /(?:^|\s)(?:т\.е|т\.д|т\.п|т\.к|т\.н|e\.g|i\.e)\.$/;

/**
 * Split text into sentences for chunked TTS synthesis.
 *
 * ICU's sentence segmentation knows what a regex cannot: the dot inside 3.14, in
 * example.com or in 1.2.3 is not a sentence end, and neither is the one before a
 * closing quote. Measured before the change: a version line arrived as four
 * fragments and a dotted domain as two, so the answer was spoken chopped up.
 * Browsers without Intl.Segmenter fall back to a scanner that ends a sentence
 * only where whitespace follows the terminator and its closing quotes.
 */
function splitIntoSentences(text) {
    if (!text) return [];
    const sentences = _splitSentences(text)
        .map(sentence => sentence.trim())
        .filter(sentence => sentence.length > 0);
    return _joinNonTerminalAbbreviations(sentences);
}

function _splitSentences(text) {
    if (typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function') {
        const lang = (typeof document !== 'undefined' && document.documentElement && document.documentElement.lang) || 'ru';
        const segmenter = new Intl.Segmenter(lang, { granularity: 'sentence' });
        return Array.from(segmenter.segment(text), part => part.segment);
    }
    return _splitSentencesWithoutSegmenter(text);
}

/** Fallback for browsers without Intl.Segmenter: cut only at real boundaries. */
function _splitSentencesWithoutSegmenter(text) {
    const closers = '»”"\')]}';
    const sentences = [];
    let start = 0;
    for (let i = 0; i < text.length; i++) {
        const char = text[i];
        // "…" is a pause, not a terminator: ICU does not break on it either, and
        // breaking there cuts "I thought… and decided" in half.
        if (char !== '.' && char !== '!' && char !== '?') continue;
        // Closing quotes and brackets belong to the sentence that just ended.
        let end = i + 1;
        while (end < text.length && closers.includes(text[end])) end++;
        // "3.14", "example.com" and "1.2.3." continue with a digit or a letter.
        if (end < text.length && !/\s/.test(text[end])) continue;
        sentences.push(text.slice(start, end));
        start = end;
        i = end - 1;
    }
    if (start < text.length) sentences.push(text.slice(start));
    return sentences;
}

/** An abbreviation that continues cannot end a sentence: keep it with what follows. */
function _joinNonTerminalAbbreviations(sentences) {
    const joined = [];
    for (const sentence of sentences) {
        const previous = joined[joined.length - 1];
        if (previous && NON_TERMINAL_ABBREVIATIONS.test(previous)) {
            joined[joined.length - 1] = `${previous} ${sentence}`;
        } else {
            joined.push(sentence);
        }
    }
    return joined;
}

/**
 * Drop fenced code before the text is split for speech.
 *
 * The markdown cleaner runs on the server, per chunk, so a fence that a sentence
 * boundary cut in half was spoken with its markers. Code is not worth reading
 * aloud, and removing it here keeps every chunk self-contained. An unterminated
 * fence drops the rest of the text — that half-fence is the same defect.
 */
function stripCodeBlocksForTTS(text) {
    if (!text) return text;
    return text
        .replace(/```[\s\S]*?```/g, ' ')
        .replace(/~~~[\s\S]*?~~~/g, ' ')
        .replace(/(?:```|~~~)[\s\S]*$/g, ' ')
        .replace(/[ \t]{2,}/g, ' ');
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
                    lang: lang,
                    gender: getVoiceGender()
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
                        lang: lang,
                        gender: getVoiceGender()
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

        // Split text into sentences (code fences first: they must never be spoken)
        const sentences = splitIntoSentences(stripCodeBlocksForTTS(text));
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
                requestBody.gender = getVoiceGender();
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
