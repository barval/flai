// static/js/chat-recording.js
// Voice recording functions

let recordTimerInterval = null;
let recordSeconds = 0;
let isRecordingLocked = false;  // Prevent double start/stop

async function toggleVoiceRecording() {
    if (isRecordingLocked) return;
    if (isRecording) {
        await stopRecording();
    } else {
        await startRecording();
    }
}

async function startRecording() {
    if (isRecordingLocked) return;
    isRecordingLocked = true;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert(t('browser_no_audio_support'));
        isRecordingLocked = false;
        return;
    }
    if (!window.isSecureContext) {
        alert(t('secure_context_required'));
        isRecordingLocked = false;
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        mediaRecorder.ondataavailable = event => {
            if (event.data.size > 0) audioChunks.push(event.data);
        };
        mediaRecorder.onstop = () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            sendVoiceMessage(audioBlob);
            stream.getTracks().forEach(track => track.stop());
        };
        mediaRecorder.start();
        isRecording = true;

        // Red background for microphone button
        const voiceBtn = document.getElementById('voice-record-button');
        voiceBtn.classList.add('recording');

        // Change send button to show recording status with timer
        const sendButton = document.getElementById('send-button');
        sendButton.disabled = true;
        sendButton.innerHTML = '<span>' + t('recording') + '</span><span class="record-timer">0с</span>';
        sendButton.classList.add('recording-mode');

        // Start timer
        recordSeconds = 0;
        if (recordTimerInterval) clearInterval(recordTimerInterval);
        recordTimerInterval = setInterval(() => {
            if (window.IS_RELOADING) {
                clearInterval(recordTimerInterval);
                return;
            }
            recordSeconds++;
            const timerSpan = sendButton.querySelector('.record-timer');
            if (timerSpan) {
                const secondsSuffix = t('seconds_suffix');
                timerSpan.textContent = recordSeconds + secondsSuffix;
            }
        }, 1000);
    } catch (err) {
        console.error('Error accessing microphone:', err);
        alert(t('microphone_access_denied'));
    } finally {
        isRecordingLocked = false;
    }
}

async function stopRecording() {
    if (isRecordingLocked) return;
    isRecordingLocked = true;

    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        isRecording = false;

        // Stop timer
        if (recordTimerInterval) {
            clearInterval(recordTimerInterval);
            recordTimerInterval = null;
        }

        // Restore microphone button
        const voiceBtn = document.getElementById('voice-record-button');
        voiceBtn.classList.remove('recording');

        // Restore send button
        const sendButton = document.getElementById('send-button');
        sendButton.disabled = false;
        sendButton.innerHTML = t('send');
        sendButton.classList.remove('recording-mode');
    }
    isRecordingLocked = false;
}

async function sendVoiceMessage(blob) {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    // Remove 'voice_' prefix as requested
    const filename = year + month + day + '_' + hours + minutes + seconds + '.webm';
    const file = new File([blob], filename, { type: 'audio/webm' });
    attachedFile = file;
    isVoiceRecorded = true;
    const preview = document.getElementById('file-preview-container');
    document.getElementById('file-preview-name').textContent = file.name;
    const fileSize = formatFileSize(file.size);
    document.getElementById('file-preview-size').textContent = ' (' + fileSize + ')';
    preview.style.display = 'block';
    sendMessage();
}