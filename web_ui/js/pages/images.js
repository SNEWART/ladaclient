// images.js — просмотр сгенерированных фото

let _imagesData = [];
let _filterKind = 'all';

registerPage('images', {
    title: 'Галерея фото',
    mount: async (el) => {
        el.innerHTML = `
            <div class="images-toolbar">
                <div class="images-filters">
                    <button class="btn small filter-btn active" data-kind="all">Все</button>
                    <button class="btn small filter-btn" data-kind="lada">Лада</button>
                    <button class="btn small filter-btn" data-kind="cat">Муся</button>
                </div>
                <div class="images-actions">
                    <span class="images-count" id="images-count">—</span>
                    <button class="btn small" onclick="loadImages(true)">🔄 Обновить</button>
                </div>
            </div>
            <div id="images-grid" class="images-grid">
                <div class="empty">Загрузка...</div>
            </div>
        `;

        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.filter-btn').forEach(b =>
                    b.classList.toggle('active', b === btn)
                );
                _filterKind = btn.dataset.kind;
                renderImages();
            });
        });

        await loadImages();
    },
});

async function loadImages(force = false) {
    const grid = document.getElementById('images-grid');
    if (!grid) return;
    if (force) grid.innerHTML = '<div class="empty">Загрузка...</div>';

    try {
        _imagesData = await api('/api/images');
        renderImages();
    } catch (e) {
        grid.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

function renderImages() {
    const grid = document.getElementById('images-grid');
    const countEl = document.getElementById('images-count');
    if (!grid) return;

    let items = _imagesData;
    if (_filterKind !== 'all') {
        items = items.filter(it => it.kind === _filterKind);
    }

    if (countEl) {
        countEl.textContent = `Всего: ${items.length}`;
    }

    if (!items.length) {
        grid.innerHTML = '<div class="empty">🖼 Пока нет сгенерированных фото</div>';
        return;
    }

    grid.innerHTML = items.map(it => `
        <div class="image-card" data-name="${escapeHtml(it.name)}">
            <a href="${it.url}" target="_blank" rel="noopener" class="image-link">
                <img src="${it.url}" alt="${escapeHtml(it.name)}" loading="lazy">
            </a>
            <div class="image-meta">
                <span class="image-kind">${it.kind === 'cat' ? '🐱' : '🧠'}</span>
                <span class="image-ts">${escapeHtml(it.timestamp)}</span>
                <span class="image-size">${it.size_kb} КБ</span>
            </div>
            <button class="image-delete" title="Удалить"
                    onclick="deleteImage('${escapeHtml(it.name)}', event)">✕</button>
        </div>
    `).join('');
}

async function deleteImage(name, event) {
    if (event) event.stopPropagation();
    if (!confirm(`Удалить ${name}?`)) return;

    try {
        await api('/api/images/' + encodeURIComponent(name), { method: 'DELETE' });
        _imagesData = _imagesData.filter(it => it.name !== name);
        renderImages();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}