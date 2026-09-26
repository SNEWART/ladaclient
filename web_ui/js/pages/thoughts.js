registerPage('thoughts', {
    title: 'Внутренние мысли',
    mount: async (el) => {
        el.innerHTML = `<div id="thoughts-list"><div class="empty">Загрузка...</div></div>`;
        try {
            const items = await api('/api/thoughts');
            const box = document.getElementById('thoughts-list');
            if (!items.length) {
                box.innerHTML = '<div class="empty">📭 Мыслей пока нет</div>';
                return;
            }
            box.innerHTML = items.map(it => `
                <div class="list-item">
                    <div class="ts">${it.timestamp}</div>
                    <div class="text">${escapeHtml(it.text)}</div>
                </div>
            `).join('');
        } catch (e) {
            document.getElementById('thoughts-list').innerHTML =
                `<div class="empty">Ошибка: ${e.message}</div>`;
        }
    },
});