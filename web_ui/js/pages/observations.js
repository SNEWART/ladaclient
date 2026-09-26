registerPage('observations', {
    title: 'Наблюдения',
    mount: async (el) => {
        el.innerHTML = `<div id="obs-list"><div class="empty">Загрузка...</div></div>`;
        try {
            const items = await api('/api/observations');
            const box = document.getElementById('obs-list');
            if (!items.length) {
                box.innerHTML = '<div class="empty">👁 Наблюдений пока нет</div>';
                return;
            }
            box.innerHTML = items.map(it => `
                <div class="exp-item">
                    <div class="ts">${it.timestamp}</div>
                    <div class="text">${escapeHtml(it.text)}</div>
                </div>
            `).join('');
        } catch (e) {
            document.getElementById('obs-list').innerHTML =
                `<div class="empty">Ошибка: ${e.message}</div>`;
        }
    },
});