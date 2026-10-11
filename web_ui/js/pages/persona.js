// persona.js — редактор файлов персоны

let _personaFiles = [];
let _personaCurrent = null;
let _personaDirty = false;

registerPage('persona', {
    title: 'Персона',
    mount: async (el) => {
        el.innerHTML = `
            <div class="persona-layout">
                <aside class="persona-files">
                    <div class="persona-files-head">
                        <span>Файлы персоны</span>
                        <button class="btn small" onclick="loadPersonaList(true)">🔄</button>
                    </div>
                    <div id="persona-files-list"><div class="empty-small">Загрузка...</div></div>
                </aside>
                <section class="persona-editor" id="persona-editor">
                    <div class="empty">Выбери файл слева</div>
                </section>
            </div>
        `;
        _personaCurrent = null;
        _personaDirty = false;
        await loadPersonaList();
    },
});

async function loadPersonaList(showLoading = false) {
    const box = document.getElementById('persona-files-list');
    if (!box) return;
    if (showLoading) box.innerHTML = '<div class="empty-small">Загрузка...</div>';
    try {
        _personaFiles = await api('/api/persona');
        if (!_personaFiles.length) {
            box.innerHTML = '<div class="empty-small">Нет .md файлов в persona/</div>';
            return;
        }
        box.innerHTML = _personaFiles.map(f => `
            <div class="persona-file ${f.name === _personaCurrent ? 'active' : ''}"
                 onclick="openPersonaFile('${escapeHtml(f.name)}')">
                <div class="persona-file-name">${escapeHtml(f.name)}</div>
                <div class="persona-file-meta">${f.size} б · ${f.modified}</div>
            </div>
        `).join('');
    } catch (e) {
        box.innerHTML = `<div class="empty-small">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

async function openPersonaFile(name) {
    if (_personaDirty && !confirm('Есть несохранённые изменения. Продолжить без сохранения?')) return;
    _personaCurrent = name;
    _personaDirty = false;
    const editor = document.getElementById('persona-editor');
    editor.innerHTML = '<div class="empty">Загрузка...</div>';
    try {
        const data = await api('/api/persona/' + encodeURIComponent(name));
        editor.innerHTML = `
            <div class="persona-editor-head">
                <div>
                    <b>${escapeHtml(data.name)}</b>
                    <span class="persona-meta">${data.size} б · ${data.modified}</span>
                </div>
                <div class="persona-editor-actions">
                    <button class="btn small" onclick="showPersonaHistory()">🕐 История</button>
                    <button class="btn small primary" id="persona-save-btn" onclick="savePersonaFile()">💾 Сохранить</button>
                </div>
            </div>
            <textarea id="persona-textarea" spellcheck="false"></textarea>
        `;
        const ta = document.getElementById('persona-textarea');
        ta.value = data.content;
        ta.addEventListener('input', () => {
            _personaDirty = true;
            const btn = document.getElementById('persona-save-btn');
            if (btn) btn.textContent = '💾 Сохранить *';
        });
        ta.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
                e.preventDefault();
                savePersonaFile();
            }
        });
        await loadPersonaList();
    } catch (e) {
        editor.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

async function savePersonaFile() {
    if (!_personaCurrent) return;
    const ta = document.getElementById('persona-textarea');
    if (!ta) return;
    const btn = document.getElementById('persona-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = '💾 Сохранение...'; }
    try {
        await api('/api/persona/' + encodeURIComponent(_personaCurrent), {
            method: 'PUT',
            body: JSON.stringify({ content: ta.value }),
        });
        _personaDirty = false;
        if (btn) btn.textContent = '✅ Сохранено';
        setTimeout(() => { if (btn) btn.textContent = '💾 Сохранить'; }, 1500);
        await loadPersonaList();
    } catch (e) {
        alert('Ошибка: ' + e.message);
        if (btn) { btn.disabled = false; btn.textContent = '💾 Сохранить'; }
    }
}

async function showPersonaHistory() {
    if (!_personaCurrent) return;
    try {
        const items = await api('/api/persona/' + encodeURIComponent(_personaCurrent) + '/history');
        if (!items.length) {
            alert('Истории версий пока нет');
            return;
        }
        const list = items.map(it =>
            `${it.timestamp}  —  ${it.size} б  —  ${it.id}`
        ).join('\n');
        const picked = prompt('Версии (скопируй ID и вставь в след. окно):\n\n' + list);
        if (!picked) return;
        if (!items.some(x => x.id === picked)) {
            alert('Такого ID нет в списке');
            return;
        }
        const view = await api('/api/persona/' + encodeURIComponent(_personaCurrent) + '/history/' + encodeURIComponent(picked));
        const preview = view.content.length > 1500
            ? view.content.slice(0, 1500) + '\n\n... (обрезано)'
            : view.content;
        if (confirm('Превью версии:\n\n' + preview + '\n\nВосстановить?')) {
            await api('/api/persona/' + encodeURIComponent(_personaCurrent) + '/restore/' + encodeURIComponent(picked), { method: 'POST' });
            alert('Восстановлено');
            await openPersonaFile(_personaCurrent);
        }
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}