let mediaRecorder;
let audioChunks = [];
let currentSessionId = null;

const Elements = {
    btnRecord: document.getElementById('btn-record'),
    btnSend: document.getElementById('btn-send'),
    btnClear: document.getElementById('btn-clear'),
    textInput: document.getElementById('text-input'),
    visualizer: document.getElementById('visualizer-container'),
    conversation: document.getElementById('conversation-container'),
    threadList: document.getElementById('thread-list'),
    statusText: document.getElementById('system-status'),
    statusPulse: document.getElementById('status-pulse'),
    
    drawer: document.getElementById('side-drawer'),
    btnCloseDrawer: document.getElementById('btn-close-drawer'),
    codeContainer: document.getElementById('drawer-code-container'),
    codeBox: document.getElementById('code-box'),
    intentContainer: document.getElementById('drawer-intent-container'),
    intentBox: document.getElementById('intent-box'),
    fileStatus: document.getElementById('drawer-file-status'),
    
    confBanner: document.getElementById('confirmation-banner'),
    confMsg: document.getElementById('conf-msg'),
    btnConfirm: document.getElementById('btn-confirm'),
    btnCancel: document.getElementById('btn-cancel'),
    btnNewChat: document.getElementById('btn-new-chat')
};

Elements.textInput.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const whisper = data.whisper_ok ? '✅' : '❌';
        const ollama = data.ollama_ok ? '✅' : '❌';
        Elements.statusText.innerHTML = `Whisper: ${whisper} &nbsp;|&nbsp; Ollama: ${ollama}`;
        Elements.statusPulse.classList.toggle('offline', !data.whisper_ok || !data.ollama_ok);
    } catch(e) {
        Elements.statusText.innerText = 'System Offline';
        Elements.statusPulse.classList.add('offline');
    }
}
fetchStatus();

// Sidebar thread logic
async function loadSessions() {
    try {
        const res = await fetch('/api/sessions');
        const data = await res.json();
        renderThreads(data.sessions);
    } catch(e) {}
}
loadSessions();

function renderThreads(sessions) {
    if (!sessions || sessions.length === 0) {
        Elements.threadList.innerHTML = '<p class="empty-state">No conversations yet.</p>';
        return;
    }
    Elements.threadList.innerHTML = '';
    sessions.forEach(session => {
        const div = document.createElement('div');
        div.className = 'thread-item' + (session.id === currentSessionId ? ' active' : '');
        
        const titleSpan = document.createElement('span');
        titleSpan.className = 'thread-text';
        titleSpan.innerText = session.topic;
        
        const metaDiv = document.createElement('div');
        metaDiv.className = 'thread-meta';
        
        const countSpan = document.createElement('span');
        countSpan.className = 'thread-count';
        countSpan.innerText = session.turn_count;
        
        const delBtn = document.createElement('button');
        delBtn.className = 'thread-del';
        delBtn.innerHTML = '🗑️';
        delBtn.title = "Delete Chat";
        delBtn.onclick = async (e) => {
            e.stopPropagation();
            await fetch('/api/sessions/' + session.id, { method: 'DELETE' });
            if (currentSessionId === session.id) startNewChat();
            loadSessions();
        };

        metaDiv.appendChild(countSpan);
        metaDiv.appendChild(delBtn);
        div.appendChild(titleSpan);
        div.appendChild(metaDiv);

        div.onclick = () => loadChatHistory(session.id);
        Elements.threadList.appendChild(div);
    });
}

async function loadChatHistory(sessionId) {
    currentSessionId = sessionId;
    loadSessions(); // re-render to set active class
    
    // reset UI
    Elements.conversation.innerHTML = '';
    hideDrawer();
    Elements.confBanner.classList.add('hidden');
    
    try {
        const res = await fetch('/api/history/' + sessionId);
        const data = await res.json();
        
        if (data.history.length === 0) {
            Elements.conversation.innerHTML = `<div class="hero-state"><h2>Empty Chat</h2></div>`;
            return;
        }
        
        data.history.forEach(turn => {
            const role = turn.role === 'user' ? 'user' : 'ai';
            const div = document.createElement('div');
            div.className = `chat-bubble chat-${role}`;
            div.innerHTML = role === 'ai' ? marked.parse(turn.content) : turn.content;
            Elements.conversation.appendChild(div);
        });
        Elements.conversation.scrollTop = Elements.conversation.scrollHeight;
    } catch(e) {}
}

function startNewChat() {
    currentSessionId = null;
    hideDrawer();
    Elements.confBanner.classList.add('hidden');
    Elements.conversation.innerHTML = `
        <div class="hero-state">
            <h2>What can I help you build today?</h2>
            <p>Commands: <code>create file</code>, <code>write code</code>, <code>summarize</code>, <code>chat</code></p>
        </div>`;
    loadSessions();
}
Elements.btnNewChat.addEventListener('click', startNewChat);

// Audio
const recordStart = async (e) => {
    if(e) e.preventDefault();
    if(mediaRecorder && mediaRecorder.state === "recording") {
        recordStop();
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            submitPipeline(audioBlob, "");
            stream.getTracks().forEach(track => track.stop());
        };
        mediaRecorder.start();
        Elements.btnRecord.classList.add('recording');
        Elements.visualizer.classList.remove('hidden');
    } catch (err) { alert("Microphone access denied."); }
};

const recordStop = (e) => {
    if(e) e.preventDefault();
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        Elements.btnRecord.classList.remove('recording');
        Elements.visualizer.classList.add('hidden');
    }
};

Elements.btnRecord.addEventListener('mousedown', recordStart);
Elements.btnRecord.addEventListener('mouseup', recordStop);

Elements.btnSend.addEventListener('click', () => {
    const text = Elements.textInput.value.trim();
    if (text) submitPipeline(null, text);
});
Elements.textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        Elements.btnSend.click();
    }
});

async function submitPipeline(audioBlob, text) {
    if (!audioBlob && !text) return;
    if (text) appendMessage('user', text);
    else appendMessage('user', '🎙️ (Audio Recording)');

    Elements.textInput.value = '';
    Elements.textInput.style.height = 'auto';

    const formData = new FormData();
    if (text) formData.append('text_input', text);
    if (audioBlob) formData.append('audio_file', audioBlob, 'record.webm');
    if (currentSessionId) formData.append('session_id', currentSessionId);

    appendMessage('ai', `<span class="loading">Thinking...</span>`);
    
    try {
        const res = await fetch('/api/pipeline', { method: 'POST', body: formData });
        const data = await res.json();
        
        const loader = Elements.conversation.querySelector('.loading');
        if (loader) loader.parentNode.remove();

        if (data.session_id) currentSessionId = data.session_id;

        handleResponse(data);
    } catch (e) {
        const loader = Elements.conversation.querySelector('.loading');
        if (loader) loader.parentNode.remove();
        appendMessage('ai', '❌ Network error hitting pipeline.');
    }
}

Elements.btnConfirm.addEventListener('click', async () => {
    Elements.confBanner.classList.add('hidden');
    appendMessage('ai', `<span class="loading">Executing code action...</span>`);
    const res = await fetch('/api/confirm', { method: 'POST' });
    const data = await res.json();
    const loader = Elements.conversation.querySelector('.loading');
    if (loader) loader.parentNode.remove();
    handleResponse(data);
});

Elements.btnCancel.addEventListener('click', async () => {
    Elements.confBanner.classList.add('hidden');
    const formData = new FormData();
    if (currentSessionId) formData.append('session_id', currentSessionId);
    
    const res = await fetch('/api/cancel', { method: 'POST', body: formData });
    await res.json();
    appendMessage('ai', '🚫 Action cancelled.');
    loadSessions();
});

Elements.btnClear.addEventListener('click', () => { Elements.textInput.value = ''; });

function handleResponse(data) {
    if (data.error) {
        appendMessage('ai', data.error);
        return;
    }

    loadSessions();

    if (data.needs_confirmation) {
        Elements.confBanner.classList.remove('hidden');
        Elements.confMsg.innerHTML = marked.parse(data.response_text);
    } else {
        Elements.confBanner.classList.add('hidden');
        appendMessage('ai', data.response_text);
    }

    if (data.code_output || data.intent_json || data.file_status) {
        showDrawer();
        if (data.file_status) {
            Elements.fileStatus.classList.remove('hidden');
            Elements.fileStatus.innerHTML = marked.parse(data.file_status);
        } else Elements.fileStatus.classList.add('hidden');

        if (data.code_output) {
            Elements.codeContainer.classList.remove('hidden');
            Elements.codeBox.textContent = data.code_output;
        } else Elements.codeContainer.classList.add('hidden');

        if (data.intent_json) {
            Elements.intentContainer.classList.remove('hidden');
            Elements.intentBox.textContent = data.intent_json;
        } else Elements.intentContainer.classList.add('hidden');
    }
}

function appendMessage(role, text) {
    const div = document.createElement('div');
    div.className = `chat-bubble chat-${role}`;
    div.innerHTML = role === 'ai' && !text.includes('<span') ? marked.parse(text) : text;
    
    const hero = Elements.conversation.querySelector('.hero-state');
    if (hero) hero.remove();

    Elements.conversation.appendChild(div);
    Elements.conversation.scrollTop = Elements.conversation.scrollHeight;
}

function showDrawer() { Elements.drawer.classList.remove('closed'); }
function hideDrawer() { Elements.drawer.classList.add('closed'); }
Elements.btnCloseDrawer.addEventListener('click', hideDrawer);
document.getElementById('btn-copy-code').addEventListener('click', () => {
    navigator.clipboard.writeText(Elements.codeBox.textContent);
    document.getElementById('btn-copy-code').innerText = 'Copied!';
    setTimeout(() => document.getElementById('btn-copy-code').innerText = 'Copy', 2000);
});
hideDrawer();
