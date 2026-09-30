// users.js — список пользователей с поиском

let _allUsers = [];

registerPage('users', {
    title: 'Пользователи',
    mount: async (el) => {
        el.innerHTML = `
            <div class="users-search">
                <input type="text"
                       id="users-search-input"
                       placeholder="Поиск по @username или ID"
                       autocomplete="off">
                <button class="btn small" onclick="loadUsers(true)">🔄</button>
            </div>
            <div id="users-list"><div class="empty">Загрузка...</div></div>
        `;

        const input = document.getElementById('users-search-input');
        input.addEventListener('input', () => filterUsers(input.value));

        await loadUsers();
    },
});

async function loadUsers(showLoading = false) {
    const box = document.getElementById('users-list');
    if (showLoading && box) box.innerHTML = '<div class="empty">Загрузка...</div>';

    try {
        _allUsers = await api('/api/users/list');
        const input = document.getElementById('users-search-input');
        filterUsers(input ? input.value : '');
    } catch (e) {
        if (box) box.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

function filterUsers(query) {
    const box = document.getElementById('users-list');
    if (!box) return;

    const q = (query || '').trim().toLowerCase();

    let items = _allUsers;
    if (q) {
        items = _allUsers.filter(u => {
            const raw = u.raw || {};
            const haystack = [
                u.display_name || '',
                raw.username || '',
                raw.first_name || '',
                raw.last_name || '',
                String(u.user_id),
            ].join(' ').toLowerCase();
            return haystack.includes(q);
        });
    }

    if (!items.length) {
        box.innerHTML = q
            ? '<div class="empty">Никого не найдено</div>'
            : '<div class="empty">📭 Пользователей пока нет</div>';
        return;
    }

    box.innerHTML = items.map(renderUserCard).join('');
}

function renderUserCard(it) {
    const raw = it.raw || {};
    const uname = raw.username ? '@' + raw.username : '';
    const name = raw.first_name ? raw.first_name : '';
    const ownerBadge = it.is_owner
        ? '<span class="badge-owner">владелец</span>'
        : '';
    const pendingBadge = it.pending
        ? `<span class="badge-pending">💗</span>`
        : '';
    const statusLabel = it.status_label || '—';

    const subParts = [];
    if (uname) subParts.push(uname);
    if (name) subParts.push(name);
    subParts.push('ID ' + it.user_id);

    return `
        <div class="user-card" onclick="openUser(${it.user_id})">
            <div class="user-card-row1">
                <span class="user-card-name">${escapeHtml(it.display_name)}</span>
                ${ownerBadge}
                ${pendingBadge}
                <span class="user-card-status">${escapeHtml(statusLabel)}</span>
            </div>
            <div class="user-card-row2">
                ${escapeHtml(subParts.join(' · '))}
            </div>
            <div class="user-card-row3">
                <span class="chip">trust <b>${it.trust.toFixed(2)}</b></span>
                <span class="chip">int <b>${it.intimacy.toFixed(2)}</b></span>
                <span class="chip">👁 ${it.observations_count}</span>
                <span class="chip">💭 ${it.emotions_count}</span>
                <span class="chip">📜 ${it.messages_count}</span>
                ${it.last_time ? `<span class="chip muted">🕐 ${escapeHtml(it.last_time)}</span>` : ''}
            </div>
        </div>
    `;
}

function openUser(uid) {
    switchPage('user_profile', { user_id: uid });
}