// static/js/chat-constants.js
// Global variables used across chat modules

let currentSessionId = window.initialSessionId;
let isSending = false;               // Flag to prevent double sending
let isSwitchingSession = false;      // Flag to block sync during session switch
let attachedFile = null;
let pendingRequests = {};
let defaultModelName = 'qwen3-vl:8b-instruct';
let sessionsData = {};
let syncInterval = null;
let newMessageIndicators = {};
let sessionQueueInfo = {};
let sessionsUpdateTimeout = null;
// Local transcribing flag for voice messages
let localTranscribingSessions = {};    // sessionId -> boolean

// Voice recording variables
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isVoiceRecorded = false;
// TTS global variables
let currentAudio = null;
let currentTTSButton = null;
let currentPlayingSessionId = null;
let currentTTSMessageText = null;  // Store text of message being played to find it later
// Message polling
let messagePollingInterval = null;
let lastMessageTimestamp = null;
let displayedMessageIds = new Set(); // IDs of messages already displayed in current session