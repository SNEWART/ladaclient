// user_profile.js — детальный профиль с управлением

const LEVEL_LABELS_SHORT = {
    0: "обычное селфи",
    1: "домашнее",
    2: "расслабленное",
    3: "уютное (плед)",
    4: "короткая одежда",
    5: "спорт / топ",
    6: "купальник",
    7: "нижнее бельё",
    8: "эротика",
    9: "NSFW",
};

const STATUS_LABELS = {
    neutral: "Никто",
    acquaintance: "Знакомый",
    lover: "Любовник",
    husband: "Муж",
};

let _profileUserId = null;
let _profileData = null;


function num(v, fallback = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
}


registerPage('user_profile', {
    title: 'Профиль',
    mount: async (el, params) => {
        let uid = params && params.user_id;

        if (!uid) {
            const raw = (window.location.hash || '').replace(/^#\/?/, '');
            const parts = raw.split('/');
            if (parts[0] === 'user_profile' && parts[1]) {
                const n = parseInt(parts[1], 10);
                if (!Number.isNaN(n)) uid = n;
            }
        }

        if (!uid) {
            el.innerHTML = `
                <div class="empty">
                    Не указан user_id.<br><br>
                    <button class="btn" onclick="switchPage('users')">← К списку пользователей</button>
                </div>`;
            return;
        }

        _profileUserId = uid;

        el.innerHTML = `
            <div class="actions">
                <button class="btn" onclick="switchPage('users')">← Назад</button>
                <button class="btn" onclick="reloadProfile()">🔄 Обновить</button>
            </div>
            <div id="profile-content"><div class="empty">Загрузка...</div></div>
        `;

        await loadProfile();
    },
});

async function loadProfile() {
    if (!_profileUserId) return;
    const box = document.getElementById('profile-content');
    if (!box) return;

    try {
        _profileData = await api(`/api/user/${_profileUserId}/profile`);
        box.innerHTML = renderProfile(_profileData);
    } catch (e) {
        box.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

async function reloadProfile() {
    await loadProfile();
}

function renderProfile(p) {
    const raw = p.raw || {};
    const subParts = [];
    if (raw.username) subParts.push('@' + raw.username);
    if (raw.first_name) subParts.push(raw.first_name);
    if (raw.last_name) subParts.push(raw.last_name);
    if (raw.phone) subParts.push('тел: ' + raw.phone);
    const subtitle = subParts.join(' · ');

    const ownerBadge = p.is_owner
        ? '<span class="badge-owner">владелец</span>'
        : '';
    const pendingBadge = p.pending
        ? `<span class="badge-pending">💗 ждёт: ${STATUS_LABELS[p.pending] || escapeHtml(p.pending)}</span>`
        : '';
    const capBadge = (p.global_cap !== null && p.global_cap !== undefined)
        ? `<span class="badge-pending">🌐 global cap ${p.global_cap}/9</span>`
        : '';

    const trustFixed = num(p.trust).toFixed(2);
    const intFixed = num(p.intimacy).toFixed(2);
    const famFixed = num(p.familiarity).toFixed(2);
    const maxLevel = num(p.max_level);
    const effMax = num(p.effective_max);
    const effLabel = LEVEL_LABELS_SHORT[effMax] || '';
    const baseLabel = LEVEL_LABELS_SHORT[maxLevel] || '';

    const overrideSet = (p.max_level_override !== null && p.max_level_override !== undefined);
    const overrideVal = overrideSet ? num(p.max_level_override) : -1;
    const overrideLabel = overrideSet
        ? `Ограничен до ${overrideVal}/9`
        : "Без ограничений (auto)";

    const statusButtons = ["neutral", "acquaintance", "lover", "husband"].map(s => {
        const active = p.status === s ? "active" : "";
        return `<button class="status-btn ${active}"
                        onclick="setUserStatus(${p.user_id}, '${s}')">${STATUS_LABELS[s]}</button>`;
    }).join("");

    const obsList = Array.isArray(p.observations) ? p.observations : [];
    const emoList = Array.isArray(p.emotions) ? p.emotions : [];
    const imgList = Array.isArray(p.images) ? p.images : [];
    const msgList = Array.isArray(p.messages) ? p.messages : [];

    return `
        <div class="profile-head">
            <div class="profile-name">
                ${escapeHtml(p.display_name || ('ID ' + p.user_id))} ${ownerBadge}
            </div>
            <div class="profile-sub">${escapeHtml(subtitle)} · ID ${p.user_id}</div>
            <div class="profile-badges">
                ${pendingBadge}
                ${capBadge}
                <span class="badge-owner">${escapeHtml(p.familiarity_label || '—')}</span>
                <span class="badge-owner">${escapeHtml(p.status_label || '—')}</span>
            </div>
        </div>

        <div class="profile-section">
            <div class="profile-section-title">⚙️ Управление</div>

            <div class="status-row">
                <span class="status-row-label">Статус:</span>
                <div class="status-buttons">${statusButtons}</div>
            </div>

            <div class="trust-slider-row">
                <label>Доверие</label>
                <input type="range" id="trust-input" min="0" max="1" step="0.05"
                       value="${num(p.trust)}"
                       oninput="updateRangeLabel('trust', this.value)">
                <span class="range-val" id="trust-range">${trustFixed}</span>
            </div>

            <div class="trust-slider-row">
                <label>Интимность</label>
                <input type="range" id="int-input" min="0" max="1" step="0.05"
                       value="${num(p.intimacy)}"
                       oninput="updateRangeLabel('int', this.value)">
                <span class="range-val" id="int-range">${intFixed}</span>
            </div>

            <div class="trust-slider-row">
                <label>Макс. уровень</label>
                <input type="range" id="ovr-input" min="-1" max="9" step="1"
                       value="${overrideVal}"
                       oninput="updateOverrideLabel(this.value)">
                <span class="range-val" id="ovr-range">${overrideLabel}</span>
                <button class="btn small" onclick="resetOverride()">auto</button>
            </div>

            <div class="profile-metrics">
                <div class="trust-metric">
                    <div class="label">Знакомство</div>
                    <div class="value">${famFixed}</div>
                </div>
                <div class="trust-metric">
                    <div class="label">Базовый</div>
                    <div class="value">${maxLevel}/9</div>
                    <div class="sub">${baseLabel}</div>
                </div>
                <div class="trust-metric">
                    <div class="label">Эффективный</div>
                    <div class="value accent">${effMax}/9</div>
                    <div class="sub">${effLabel}</div>
                </div>
            </div>

            <div class="actions-row">
                <button class="btn primary" onclick="saveProfileTrust()">💾 Сохранить</button>
            </div>
        </div>

        <div class="profile-section">
            <div class="profile-section-title">👁 Наблюдений: ${num(p.observations_count)}</div>
            <div class="profile-list">
                ${obsList.length === 0
                    ? '<div class="empty-small">Нет записей</div>'
                    : obsList.slice(0, 40).map(o => `
                        <div class="profile-list-item">
                            <div class="ts">${o.timestamp || ''}</div>
                            <div class="text">${escapeHtml(o.text || '')}</div>
                        </div>
                    `).join('')}
            </div>
        </div>

        <div class="profile-section">
            <div class="profile-section-title">💭 Эмоций: ${num(p.emotions_count)}</div>
            <div class="profile-list">
                ${emoList.length === 0
                    ? '<div class="empty-small">Нет записей</div>'
                    : emoList.slice(0, 40).map(o => `
                        <div class="profile-list-item">
                            <div class="ts">${o.timestamp || ''}</div>
                            <div class="text">${escapeHtml(o.text || '')}</div>
                        </div>
                    `).join('')}
            </div>
        </div>

        <div class="profile-section">
            <div class="profile-section-title">🖼 Фото: ${num(p.images_count)}</div>
            <div class="profile-list">
                ${imgList.length === 0
                    ? '<div class="empty-small">Нет записей</div>'
                    : imgList.slice(0, 20).map(o => `
                        <div class="profile-list-item">
                            <div class="ts">${o.timestamp || ''}</div>
                            <div class="text">${escapeHtml(o.text || '')}</div>
                        </div>
                    `).join('')}
            </div>
        </div>

        <div class="profile-section">
            <div class="profile-section-title">📜 Сообщений: ${num(p.messages_count)}</div>
            <div class="profile-list profile-dialogs">
                ${msgList.length === 0
                    ? '<div class="empty-small">Нет сообщений</div>'
                    : msgList.slice(-50).map(m => `
                        <div class="profile-dialog-msg ${m.role}">
                            <div class="who">
                                <span>
                                    ${m.role === 'user' ? '👤 Пользователь'
                                      : m.role === 'assistant' ? '🧠 Лада'
                                      : '⚙️ Система'}
                                </span>
                                <span class="ts">${m.timestamp || ''}</span>
                            </div>
                            <div class="text">${escapeHtml(m.content || '')}</div>
                        </div>
                    `).join('')}
            </div>
        </div>
    `;
}

function updateRangeLabel(kind, value) {
    const v = parseFloat(value).toFixed(2);
    const el = document.getElementById(`${kind}-range`);
    if (el) el.textContent = v;
}

function updateOverrideLabel(value) {
    const v = parseInt(value);
    const el = document.getElementById('ovr-range');
    if (!el) return;
    if (v < 0) {
        el.textContent = "Без ограничений (auto)";
    } else {
        el.textContent = `Ограничен до ${v}/9`;
    }
}

async function saveProfileTrust() {
    if (!_profileUserId) return;
    const trustEl = document.getElementById('trust-input');
    const intEl = document.getElementById('int-input');
    const ovrEl = document.getElementById('ovr-input');
    if (!trustEl || !intEl || !ovrEl) return;

    const trust = parseFloat(trustEl.value);
    const intimacy = parseFloat(intEl.value);
    const ovrRaw = parseInt(ovrEl.value);
    const ovrVal = (ovrRaw < 0) ? null : ovrRaw;

    try {
        await api('/api/trust/set', {
            method: 'POST',
            body: JSON.stringify({
                user_id: _profileUserId,
                trust: trust,
                intimacy: intimacy,
            })
        });
        await api('/api/trust/max_level', {
            method: 'POST',
            body: JSON.stringify({ user_id: _profileUserId, max_level: ovrVal })
        });
        await loadProfile();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function resetOverride() {
    if (!_profileUserId) return;
    try {
        await api('/api/trust/max_level', {
            method: 'POST',
            body: JSON.stringify({ user_id: _profileUserId, max_level: null })
        });
        await loadProfile();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function setUserStatus(uid, status) {
    try {
        await api('/api/trust/status', {
            method: 'POST',
            body: JSON.stringify({ user_id: uid, status: status })
        });
        await loadProfile();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}