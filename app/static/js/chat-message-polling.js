// static/js/chat-message-polling.js
// Periodic polling for new messages in the current session

let messagePollingInterval = null;
let lastMessageTimestamp = null;

function startMessagePolling() {
    if (messagePollingInterval) clearInterval(messagePollingInterval);
    // Poll every 5 seconds
    messagePollingInterval = setInterval(pollNewMessages, 5000);
}

function stopMessagePolling() {
    if (messagePollingInterval) {
        clearInterval(messagePollingInterval);
        messagePollingInterval = null;
    }
}

async function pollNewMessages() {
    if (window.IS_RELOADING || !currentSessionId) return;

    // Get the timestamp of the last displayed message in the current session
    const messagesContainer = document.getElementById('chat-messages');
    const lastMessageEl = messagesContainer.lastElementChild;
    if (lastMessageEl && lastMessageEl.dataset.timestamp) {
        lastMessageTimestamp = lastMessageEl.dataset.timestamp;
    } else {
        // No messages yet, skip polling
        return;
    }

    try {
        const response = await fetch(`/api/sessions/${currentSessionId}/messages?since=${encodeURIComponent(lastMessageTimestamp)}`);
        if (!response.ok) {
            console.error('Failed to fetch new messages:', response.status);
            return;
        }
        const newMessages = await response.json();
        if (newMessages.length > 0) {
            // Display each new message
            for (const msg of newMessages) {
                let responseTime = null;
                if (msg.response_time) {
                    if (typeof msg.response_time === 'object') {
                        responseTime = msg.response_time;
                    } else if (!isNaN(parseFloat(msg.response_time))) {
                        responseTime = parseFloat(msg.response_time);
                    }
                }
                let mmTime = msg.mm_time;
                let genTime = msg.gen_time;
                let mmModel = msg.mm_model;
                let genModel = msg.gen_model;
                if (mmTime && genTime) {
                    responseTime = {
                        mm_time: parseFloat(mmTime),
                        gen_time: parseFloat(genTime),
                        mm_model: mmModel || 'unknown',
                        gen_model: genModel || 'unknown'
                    };
                }
                displayMessage(
                    msg.role,
                    msg.content,
                    msg.file_data,
                    msg.file_type,
                    msg.file_name,
                    msg.timestamp,
                    responseTime,
                    msg.model_name,
                    mmTime,
                    genTime,
                    mmModel,
                    genModel
                );
            }
            // Update last visit timestamp
            updateLastVisit(currentSessionId);
        }
    } catch (err) {
        console.error('Error polling new messages:', err);
    }
}

// Override loadMessages to reset polling after switching session
const originalLoadMessages = window.loadMessages;
window.loadMessages = function(sessionId) {
    return originalLoadMessages(sessionId).then(() => {
        // Restart polling for the new session
        stopMessagePolling();
        startMessagePolling();
    });
};