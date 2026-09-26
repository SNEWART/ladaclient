let queueInterval = null;

registerPage('queue', {
    title: 'Очередь сообщений',
    mount: async (el) => {
        el.innerHTML = `
            <div class="actions">
                <button class="btn danger" onclick="clearQueue()">🗑 Очистить всю очередь</button>
                <button class="btn" onclick="loadQueue()">🔄 Обновить</button>
            </div>
            <div id="queue-list"><div class="empty">Загрузка...</div></div>
        `;
        await loadQueue();
        queueInterval = setInterval(loadQueue, 3000);
    },
});

async function loadQueue() {
    try {
        const q = await api('/api/queue');
        const box = document.getElementById('queue-list');
        if (!box) return;
        if (!q.items.length) {
            box.innerHTML = '<div class="empty">📭 Очередь пуста</div>';
            return;
        }
        box.innerHTML = q.items.map(it => `
            <div class="queue-item">
                <div class="num">${it.index}</div>
                <div class="body">
                    <div class="head">
                        <span class="user-name">${escapeHtml(it.username)}</span>
                        <span>ID ${it.user_id} · ${it.enqueued_at}</span>
                    </div>
                    <div class="text">${escapeHtml(it.text) || '<i style="color:#666">без текста</i>'}</div>
                </div>
                <div class="right">
                    <div class="wait">⌛ ${it.wait_seconds}с</div>
                    <button class="remove-btn" onclick="removeFromQueue(${it.index})">Удалить</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        const box = document.getElementById('queue-list');
        if (box) box.innerHTML = `<div class="empty">Ошибка: ${e.message}</div>`;
    }
    updateQueueBadge();
}

async function removeFromQueue(index) {
    try {
        await api('/api/queue/remove', { method: 'POST', body: JSON.stringify({ index }) });
        loadQueue();
    } catch (e) { alert('Ошибка: ' + e.message); }
}

async function clearQueue() {
    if (!confirm('Удалить все сообщения из очереди?')) return;
    try {
        const r = await api('/api/queue/clear', { method: 'POST' });
        alert(`Удалено: ${r.removed}`);
        loadQueue();
    } catch (e) { alert('Ошибка: ' + e.message); }
}