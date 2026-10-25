// memory.js — обзор и управление памятью

let _memState = {
    offset: 0,
    limit: 100,
    total: 0,
    filters: {
        category: "",
        user_id: "all",
        q: "",
        valence_mode: "any",
        has_meta: "",
    },
};

registerPage('memory', {
    title: 'Память',
    mount: async (el) => {
        el.innerHTML = `
            <div class="mem-stats" id="mem-stats">—</div>
            <div class="mem-toolbar">
                <div class="mem-filters">
                    <select id="mem-cat"><option value="">Все категории</option></select>
                    <select id="mem-user"><option value="all">Все пользователи</option></select>
                    <select id="mem-meta">
                        <option value="">Все записи</option>
                        <option value="yes">С эмоциями (v)</option>
                        <option value="no">Без эмоций</option>
                    </select>
                    <select id="mem-valence">
                        <option value="any">Любая валентность</option>
                        <option value="pos">Позитивные (v > 0.1)</option>
                        <option value="neg">Негативные (v < -0.1)</option>
                        <option value="neu">Нейтральные</option>
                    </select>
                    <input type="text" id="mem-q" placeholder="Поиск по тексту...">
                    <button class="btn small" onclick="memReload(true)">🔄</button>
                </div>
                <div class="mem-count" id="mem-count">—</div>
            </div>
            <div id="mem-list" class="mem-list"><div class="empty">Загрузка...</div></div>
            <div id="mem-pager" class="mem-pager"></div>
        `;

        // Наполняем фильтры
        try {
            const f = await api('/api/memory/filters');
            const catSel = document.getElementById('mem-cat');
            f.categories.forEach(c => {
                const o = document.createElement('option');
                o.value = c; o.textContent = c;
                catSel.appendChild(o);
            });
            const userSel = document.getElementById('mem-user');
            f.users.forEach(u => {
                const o = document.createElement('option');
                o.value = u.user_id;
                o.textContent = `${u.name} (ID ${u.user_id})`;
                userSel.appendChild(o);
            });
        } catch (e) {}

        // Обработчики
        document.getElementById('mem-cat').addEventListener('change', () => memReload(true));
        document.getElementById('mem-user').addEventListener('change', () => memReload(true));
        document.getElementById('mem-meta').addEventListener('change', () => memReload(true));
        document.getElementById('mem-valence').addEventListener('change', () => memReload(true));

        let _qTimer = null;
        document.getElementById('mem-q').addEventListener('input', () => {
            clearTimeout(_qTimer);
            _qTimer = setTimeout(() => memReload(true), 400);
        });

        await memLoadStats();
        await memLoad();
    },
});

async function memLoadStats() {
    const el = document.getElementById('mem-stats');
    if (!el) return;
    try {
        const s = await api('/api/memory/stats');
        const cats = Object.entries(s.by_category)
            .map(([k, v]) => `${k}: ${v}`).join(' · ');
        el.innerHTML = `
            <div class="mem-stat-row">
                <span class="mem-stat-big">${s.total}</span>
                <span class="mem-stat-label">записей</span>
                <span class="mem-stat-sep">·</span>
                <span>${s.with_meta} с эмоциями / ${s.without_meta} без</span>
                <span class="mem-stat-sep">·</span>
                <span class="mem-valence-pos">+${s.valence_pos}</span>
                <span class="mem-valence-neu">${s.valence_neutral}</span>
                <span class="mem-valence-neg">−${s.valence_neg}</span>
            </div>
            <div class="mem-stat-cats">${cats}</div>
        `;
    } catch (e) {
        el.textContent = '—';
    }
}

async function memReload(resetOffset = false) {
    if (resetOffset) _memState.offset = 0;
    await memLoad();
}

async function memLoad() {
    const list = document.getElementById('mem-list');
    const count = document.getElementById('mem-count');
    if (!list) return;
    list.innerHTML = '<div class="empty">Загрузка...</div>';

    const f = _memState.filters;
    f.category = document.getElementById('mem-cat').value;
    f.user_id = document.getElementById('mem-user').value;
    f.q = document.getElementById('mem-q').value.trim();
    f.has_meta = document.getElementById('mem-meta').value;

    const valMode = document.getElementById('mem-valence').value;

    const params = new URLSearchParams();
    if (f.category) params.set('category', f.category);
    if (f.user_id && f.user_id !== 'all') params.set('user_id', f.user_id);
    if (f.q) params.set('q', f.q);
    if (f.has_meta) params.set('has_meta', f.has_meta);
    if (valMode === 'pos') params.set('valence_min', '0.1');
    if (valMode === 'neg') params.set('valence_max', '-0.1');
    if (valMode === 'neu') {
        params.set('valence_min', '-0.1');
        params.set('valence_max', '0.1');
    }
    params.set('limit', _memState.limit);
    params.set('offset', _memState.offset);

    try {
        const data = await api('/api/memory/list?' + params.toString());
        _memState.total = data.total;

        if (count) {
            count.textContent = `Показано ${data.items.length} из ${data.total}`;
        }

        if (!data.items.length) {
            list.innerHTML = '<div class="empty">Ничего не найдено</div>';
            document.getElementById('mem-pager').innerHTML = '';
            return;
        }

        list.innerHTML = data.items.map((it, idx) => memRenderRow(it, idx)).join('');
        memRenderPager();
    } catch (e) {
        list.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

function memRenderRow(it, idx) {
    const v = it.valence;
    let vClass = "v-none";
    let vText = "—";
    if (v !== null && v !== undefined) {
        const vf = parseFloat(v);
        if (vf > 0.1) { vClass = "v-pos"; }
        else if (vf < -0.1) { vClass = "v-neg"; }
        else { vClass = "v-neu"; }
        vText = (vf >= 0 ? "+" : "") + vf.toFixed(2);
    }

    const metaBits = [];
    if (it.mood_at_time) metaBits.push(`настр: ${it.mood_at_time}`);
    if (it.trust_at_time !== null && it.trust_at_time !== undefined) {
        metaBits.push(`trust: ${Number(it.trust_at_time).toFixed(2)}`);
    }
    if (it.intimacy_at_time !== null && it.intimacy_at_time !== undefined) {
        metaBits.push(`int: ${Number(it.intimacy_at_time).toFixed(2)}`);
    }

    const textEsc = escapeHtml(it.text);
    const dataAttrs = `
        data-text="${textEsc.replace(/"/g, '&quot;')}"
        data-uid="${it.user_id === null ? '' : it.user_id}"
        data-ts="${escapeHtml(it.timestamp)}"
    `;

    return `
        <div class="mem-row" ${dataAttrs}>
            <div class="mem-row-head">
                <span class="mem-cat mem-cat-${escapeHtml(it.category)}">${escapeHtml(it.category)}</span>
                <span class="mem-val ${vClass}" title="valence">${vText}</span>
                <span class="mem-user">${escapeHtml(it.user_name)}</span>
                <span class="mem-ts">${escapeHtml(it.timestamp)}</span>
                <span class="mem-row-actions">
                    <button class="files-action" title="Редактировать"
                            onclick="memEditRow(this, event)">✏️</button>
                    <button class="files-action danger" title="Удалить"
                            onclick="memDeleteRow(this, event)">🗑</button>
                </span>
            </div>
            <div class="mem-text">${textEsc}</div>
            ${metaBits.length ? `<div class="mem-meta-line">${escapeHtml(metaBits.join(' · '))}</div>` : ''}
        </div>
    `;
}

function memRenderPager() {
    const pager = document.getElementById('mem-pager');
    if (!pager) return;
    const total = _memState.total;
    const limit = _memState.limit;
    const offset = _memState.offset;
    const page = Math.floor(offset / limit) + 1;
    const pages = Math.max(1, Math.ceil(total / limit));

    const btns = [];
    if (offset > 0) {
        btns.push(`<button class="btn small" onclick="memPage(${offset - limit})">← Назад</button>`);
    }
    btns.push(`<span class="mem-page-info">Стр. ${page} из ${pages}</span>`);
    if (offset + limit < total) {
        btns.push(`<button class="btn small" onclick="memPage(${offset + limit})">Вперёд →</button>`);
    }
    pager.innerHTML = btns.join(' ');
}

function memPage(newOffset) {
    _memState.offset = Math.max(0, newOffset);
    memLoad();
    document.querySelector('.content').scrollTop = 0;
}

function _rowKeyFromBtn(btn) {
    const row = btn.closest('.mem-row');
    return {
        text: row.dataset.text || '',
        user_id: row.dataset.uid ? parseInt(row.dataset.uid, 10) : null,
        timestamp: row.dataset.ts || '',
    };
}

async function memDeleteRow(btn, event) {
    if (event) event.stopPropagation();
    const key = _rowKeyFromBtn(btn);
    if (!confirm(`Удалить эту запись?\n\n"${key.text.slice(0, 120)}"`)) return;
    try {
        await api('/api/memory/delete', {
            method: 'POST',
            body: JSON.stringify(key),
        });
        await memLoadStats();
        await memLoad();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function memEditRow(btn, event) {
    if (event) event.stopPropagation();
    const key = _rowKeyFromBtn(btn);
    const newText = prompt('Новый текст (оставь пустым чтобы отменить):', key.text);
    if (newText === null) return;
    if (!newText.trim() || newText === key.text) return;

    try {
        await api('/api/memory/edit', {
            method: 'POST',
            body: JSON.stringify({ ...key, new_text: newText.trim() }),
        });
        await memLoad();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}