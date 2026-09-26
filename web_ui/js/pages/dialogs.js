let currentDialogUserId = null;

registerPage('dialogs', {
    title: 'История переписок',
    mount: async (el) => {
        el.innerHTML = `
            <div id="dialogs-view">
                <div id="dialogs-list"><div class="empty">Загрузка...</div></div>
            </div>
            <div id="dialog-view" style="display:none;">
                <div class="actions">
                    <button class="btn" onclick="backToDialogs()">← Назад к списку</button>
                    <button class="btn danger" onclick="clearCurrentDialog()">🗑 Удалить переписку</button>
                </div>
                <div id="dialog-header" style="margin-bottom: 16px; color: var(--muted); font-size: 14px;"></div>
                <div class="chat-box" id="dialog-messages"></div>
            </div>
        `;
        currentDialogUserId = null;
        await loadDialogs();
    },
});

function userLabel(it) {
    if (it.display_name) return it.display_name;
    return `ID ${it.user_id}`;
}

function userSubtitle(it) {
    const raw = it.raw || {};
    const parts = [];
    if (raw.username) parts.push('@' + raw.username);
    parts.push(`ID ${it.user_id}`);
    return parts.join(' · ');
}

async function loadDialogs() {
    try {
        const items = await api('/api/dialogs');
        const box = document.getElementById('dialogs-list');
        document.getElementById('dialogs-view').style.display = 'block';
        document.getElementById('dialog-view').style.display = 'none';
        if (!items.length) {
            box.innerHTML = '<div class="empty">📭 Переписок пока нет</div>';
            return;
        }
        box.innerHTML = items.map(it => `
            <div class="dialog-item" onclick="openDialog(${it.user_id})">
                <div class="head">
                    <span class="user-id">${escapeHtml(userLabel(it))}</span>
                    <span class="meta">${it.messages_count} сообщ. · ${it.last_time}</span>
                </div>
                <div class="preview">
                    <span class="role">${it.last_role === 'user' ? '👤' : '🧠'}</span>
                    ${escapeHtml(it.last_text)}
                </div>
                <div class="meta" style="margin-top:4px; font-size:12px; color: var(--muted);">
                    ${escapeHtml(userSubtitle(it))}
                </div>
            </div>
        `).join('');
    } catch (e) {
        document.getElementById('dialogs-list').innerHTML =
            `<div class="empty">Ошибка: ${e.message}</div>`;
    }
}

async function openDialog(userId) {
    currentDialogUserId = userId;
    document.getElementById('dialogs-view').style.display = 'none';
    document.getElementById('dialog-view').style.display = 'block';
    const header = document.getElementById('dialog-header');
    const box = document.getElementById('dialog-messages');
    header.textContent = 'Загрузка...';
    box.innerHTML = '<div class="empty">...</div>';
    try {
        const items = await api(`/api/dialogs/${userId}`);
        const allDialogs = await api('/api/dialogs');
        const info = allDialogs.find(d => d.user_id === userId);
        const name = info ? userLabel(info) : `ID ${userId}`;
        const sub = info ? userSubtitle(info) : '';
        header.innerHTML = `<b>${escapeHtml(name)}</b> · ${escapeHtml(sub)} · сообщений: ${items.length}`;

        if (!items.length) {
            box.innerHTML = '<div class="empty">📭 Пусто</div>';
            return;
        }
        box.innerHTML = items.map(m => `
            <div class="dialog-msg ${m.role}">
                <div class="who">
                    ${m.role === 'user' ? '👤 Пользователь' : '🧠 Лада'}
                    <span class="when">${m.timestamp}</span>
                </div>
                <div class="body">${escapeHtml(m.content)}</div>
            </div>
        `).join('');
    } catch (e) {
        header.textContent = 'Ошибка';
        box.innerHTML = `<div class="empty">Ошибка: ${e.message}</div>`;
    }
}

function backToDialogs() {
    currentDialogUserId = null;
    loadDialogs();
}

async function clearCurrentDialog() {
    if (!currentDialogUserId) return;
    if (!confirm(`Удалить всю переписку с ID ${currentDialogUserId}?`)) return;
    try {
        await api(`/api/dialogs/${currentDialogUserId}/clear`, { method: 'POST' });
        alert('Переписка удалена');
        backToDialogs();
    } catch (e) { alert('Ошибка: ' + e.message); }
}