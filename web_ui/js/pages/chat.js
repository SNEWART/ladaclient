registerPage('chat', {
    title: 'Чат с Ладой',
    mount: async (el) => {
        el.innerHTML = `
            <div class="chat-box" id="chat-box"></div>
            <div class="chat-input-row">
                <input type="text" id="chat-input" placeholder="Напиши Ладе..." autocomplete="off">
                <button id="chat-send">Отправить</button>
            </div>
        `;
        const input = document.getElementById('chat-input');
        const send = document.getElementById('chat-send');
        send.addEventListener('click', sendChat);
        input.addEventListener('keypress', e => { if (e.key === 'Enter') sendChat(); });
        await loadChatHistory();
        input.focus();
    },
});

async function loadChatHistory() {
    try {
        const items = await api('/api/chat/history');
        const box = document.getElementById('chat-box');
        box.innerHTML = items.map(m =>
            `<div class="msg ${m.role === 'user' ? 'user' : 'bot'}">${escapeHtml(m.content)}</div>`
        ).join('');
        document.querySelector('.content').scrollTop = 999999;
    } catch (e) {
        document.getElementById('chat-box').innerHTML =
            `<div class="empty">Ошибка: ${e.message}</div>`;
    }
}

async function sendChat() {
    const input = document.getElementById('chat-input');
    const send = document.getElementById('chat-send');
    const box = document.getElementById('chat-box');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    send.disabled = true;
    box.insertAdjacentHTML('beforeend', `<div class="msg user">${escapeHtml(text)}</div>`);
    const thinking = document.createElement('div');
    thinking.className = 'msg bot';
    thinking.textContent = '...';
    box.appendChild(thinking);
    document.querySelector('.content').scrollTop = 999999;
    try {
        const r = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message: text }) });
        thinking.textContent = r.reply;
    } catch (e) {
        thinking.textContent = '⚠️ Ошибка: ' + e.message;
    } finally {
        send.disabled = false;
        input.focus();
        document.querySelector('.content').scrollTop = 999999;
    }
}