registerPage('dashboard', {
    title: 'Дашборд',
    mount: async (el) => {
        el.innerHTML = `
            <div class="grid" id="stats-grid"></div>
            <div class="actions">
                <button class="btn primary" onclick="doThink()">💭 Мысль</button>
                <button class="btn" onclick="doReflect()">🪞 Рефлексия</button>
                <button class="btn" onclick="doSleep(false)">💤 Сон</button>
                <button class="btn" onclick="doSleep(true)">💤 Сон (форс)</button>
                <button class="btn" onclick="toggleMode()">⚙️ Режим</button>
            </div>
        `;
        try {
            const s = await api('/api/status');
            const cards = [
                ['FAISS всего', s.faiss.total],
                ['Наблюдения', s.faiss.observation],
                ['Эмоции', s.faiss.emotion],
                ['Рефлексии', s.faiss.reflection],
                ['Монологи', s.faiss.monologue],
                ['Юзеров в памяти', s.users ? s.users.length : 0],
                ['Персона (симв.)', s.persona_size],
                ['Настроение', s.mood],
            ];
            document.getElementById('stats-grid').innerHTML = cards.map(([label, value]) =>
                `<div class="card"><div class="label">${label}</div><div class="value" style="font-size:18px">${value}</div></div>`
            ).join('');
        } catch (e) {
            el.innerHTML += `<div class="empty">Ошибка: ${e.message}</div>`;
        }
    },
});

async function doThink() {
    try { const r = await api('/api/think', { method: 'POST' }); alert('💭 ' + r.text); }
    catch (e) { alert('Ошибка: ' + e.message); }
}
async function doReflect() {
    try { await api('/api/reflect', { method: 'POST' }); alert('🪞 Рефлексия записана'); }
    catch (e) { alert('Ошибка: ' + e.message); }
}
async function doSleep(force) {
    try {
        const r = await api('/api/sleep', {
            method: 'POST',
            body: JSON.stringify({ force: !!force })
        });
        alert('💤 ' + r.result);
    } catch (e) { alert('Ошибка: ' + e.message); }
}
async function toggleMode() {
    try {
        const s = await api('/api/status');
        const next = s.mode === 'all' ? 'owner' : 'all';
        await api('/api/mode', { method: 'POST', body: JSON.stringify({ mode: next }) });
        refreshHeaderStatus();
    } catch (e) { alert('Ошибка: ' + e.message); }
}