// trust.js — ручное управление trust, intimacy, статусом и оверрайдом

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

registerPage('trust', {
    title: 'Управление доверием',
    mount: async (el) => {
        el.innerHTML = `
            <div class="actions">
                <button class="btn" onclick="loadTrusts()">🔄 Обновить</button>
            </div>
            <div id="trust-list"><div class="empty">Загрузка...</div></div>
        `;
        await loadTrusts();
    },
});

async function loadTrusts() {
    try {
        const items = await api('/api/trust');
        const box = document.getElementById('trust-list');
        if (!box) return;
        if (!items.length) {
            box.innerHTML = '<div class="empty">📭 Пользователей пока нет</div>';
            return;
        }
        box.innerHTML = items.map(renderTrustItem).join('');
    } catch (e) {
        const box = document.getElementById('trust-list');
        if (box) box.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

function renderTrustItem(it) {
    const eff = (it.effective_max !== undefined) ? it.effective_max : it.max_level;
    const effLabel = LEVEL_LABELS_SHORT[eff] || "—";
    const baseLabel = LEVEL_LABELS_SHORT[it.max_level] || "—";
    const trustFixed = it.trust.toFixed(2);
    const intFixed = it.intimacy.toFixed(2);
    const famFixed = it.familiarity.toFixed(2);
    const moodNote = it.mood ? `настроение: ${it.mood}` : "";
    const override = (it.max_level_override !== null && it.max_level_override !== undefined)
        ? it.max_level_override
        : "";
    const overrideSet = override !== "";
    const ownerBadge = it.is_owner ? `<span class="badge-owner">владелец</span>` : "";
    const currentStatus = it.status || "neutral";
    const pending = it.pending;
    const pendingBadge = pending
        ? `<span class="badge-pending">💗 ждёт ответа: ${STATUS_LABELS[pending] || pending}</span>`
        : "";

    const overrideLabel = overrideSet
        ? `Ограничен до ${override}/9`
        : "Без ограничений (auto)";

    const statusButtons = ["neutral", "acquaintance", "lover", "husband"].map(s => {
        const active = currentStatus === s ? "active" : "";
        const label = STATUS_LABELS[s];
        return `<button class="status-btn ${active}" onclick="setUserStatus(${it.user_id}, '${s}')">${label}</button>`;
    }).join("");

    return `
        <div class="trust-item" data-uid="${it.user_id}">
            <div class="head">
                <span class="name">${escapeHtml(it.display_name)}</span>
                <span class="uid">ID ${it.user_id} ${ownerBadge}</span>
            </div>
            ${pendingBadge}

            <div class="metrics">
                <div class="trust-metric">
                    <div class="label">Знакомство</div>
                    <div class="value">${famFixed}</div>
                </div>
                <div class="trust-metric">
                    <div class="label">Доверие</div>
                    <div class="value">${trustFixed}</div>
                </div>
                <div class="trust-metric">
                    <div class="label">Интимность</div>
                    <div class="value">${intFixed}</div>
                </div>
                <div class="trust-metric">
                    <div class="label">Базовый уровень</div>
                    <div class="value">${it.max_level}/9</div>
                    <div class="sub">${baseLabel}</div>
                </div>
                <div class="trust-metric">
                    <div class="label">Эффективный</div>
                    <div class="value accent">${eff}/9</div>
                    <div class="sub">${effLabel}${moodNote ? " · " + moodNote : ""}</div>
                </div>
            </div>

            <div class="status-row">
                <span class="status-row-label">Статус:</span>
                <div class="status-buttons">${statusButtons}</div>
            </div>

            <div class="trust-slider-row">
                <label>Доверие</label>
                <input type="range"
                       id="trust-input-${it.user_id}"
                       min="0" max="1" step="0.05"
                       value="${it.trust}"
                       oninput="updateTrustLabel(${it.user_id}, 'trust', this.value)">
                <span class="range-val" id="trust-range-${it.user_id}">${trustFixed}</span>
            </div>

            <div class="trust-slider-row">
                <label>Интимность</label>
                <input type="range"
                       id="int-input-${it.user_id}"
                       min="0" max="1" step="0.05"
                       value="${it.intimacy}"
                       oninput="updateTrustLabel(${it.user_id}, 'int', this.value)">
                <span class="range-val" id="int-range-${it.user_id}">${intFixed}</span>
            </div>

            <div class="trust-slider-row">
                <label>Макс. уровень</label>
                <input type="range"
                       id="ovr-input-${it.user_id}"
                       min="-1" max="9" step="1"
                       value="${overrideSet ? override : -1}"
                       oninput="updateOverrideLabel(${it.user_id}, this.value)">
                <span class="range-val" id="ovr-range-${it.user_id}">${overrideLabel}</span>
                <button class="btn small" onclick="clearOverride(${it.user_id})">auto</button>
            </div>

            <div class="actions-row">
                <button class="btn primary" onclick="saveTrust(${it.user_id})">💾 Сохранить</button>
            </div>
        </div>
    `;
}

function updateTrustLabel(uid, kind, value) {
    const v = parseFloat(value).toFixed(2);
    const rangeEl = document.getElementById(`${kind}-range-${uid}`);
    if (rangeEl) rangeEl.textContent = v;
}

function updateOverrideLabel(uid, value) {
    const v = parseInt(value);
    const el = document.getElementById(`ovr-range-${uid}`);
    if (!el) return;
    if (v < 0) {
        el.textContent = "Без ограничений (auto)";
    } else {
        el.textContent = `Ограничен до ${v}/9`;
    }
}

async function saveTrust(uid) {
    const trustEl = document.getElementById(`trust-input-${uid}`);
    const intEl = document.getElementById(`int-input-${uid}`);
    const ovrEl = document.getElementById(`ovr-input-${uid}`);
    if (!trustEl || !intEl || !ovrEl) {
        alert("Ошибка: слайдеры не найдены");
        return;
    }

    const trust = parseFloat(trustEl.value);
    const intimacy = parseFloat(intEl.value);
    const ovrRaw = parseInt(ovrEl.value);
    const ovrVal = (ovrRaw < 0) ? null : ovrRaw;

    if (Number.isNaN(trust) || Number.isNaN(intimacy)) {
        alert("Ошибка: некорректные значения trust/intimacy");
        return;
    }

    try {
        await api('/api/trust/set', {
            method: 'POST',
            body: JSON.stringify({
                user_id: uid,
                trust: trust,
                intimacy: intimacy,
            })
        });
        await api('/api/trust/max_level', {
            method: 'POST',
            body: JSON.stringify({ user_id: uid, max_level: ovrVal })
        });
        await loadTrusts();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function clearOverride(uid) {
    try {
        await api('/api/trust/max_level', {
            method: 'POST',
            body: JSON.stringify({ user_id: uid, max_level: null })
        });
        await loadTrusts();
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
        await loadTrusts();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}