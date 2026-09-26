registerPage('reflections', {
    title: 'Рефлексии',
    mount: async (el) => {
        el.innerHTML = `<div id="reflections-list"><div class="empty">Загрузка...</div></div>`;
        try {
            const items = await api('/api/reflections');
            const box = document.getElementById('reflections-list');
            if (!items.length) {
                box.innerHTML = '<div class="empty">📭 Рефлексий пока нет</div>';
                return;
            }
            box.innerHTML = items.map(it => `
                <div class="list-item">
                    <div class="ts">${it.timestamp}</div>
                    <div class="text">${escapeHtml(it.text)}</div>
                </div>
            `).join('');
        } catch (e) {
            document.getElementById('reflections-list').innerHTML =
                `<div class="empty">Ошибка: ${e.message}</div>`;
        }
    },
});