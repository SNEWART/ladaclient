// files.js — файловый менеджер

let _filesCurrentPath = "";
let _filesItems = [];
let _filesEditorPath = null;
let _filesEditorDirty = false;

registerPage('files', {
    title: 'Файлы',
    mount: async (el) => {
        el.innerHTML = `
            <div class="files-toolbar">
                <div class="files-breadcrumb" id="files-breadcrumb"></div>
                <div class="files-actions">
                    <button class="btn small" onclick="filesPromptMkdir()">📁 Папка</button>
                    <label class="btn small files-upload-btn">
                        ⬆ Загрузить
                        <input type="file" id="files-upload-input"
                               style="display:none" multiple>
                    </label>
                    <button class="btn small" onclick="filesReload()">🔄</button>
                </div>
            </div>
            <div id="files-list" class="files-list">
                <div class="empty">Загрузка...</div>
            </div>
            <div id="files-editor" class="files-editor" style="display:none"></div>
        `;

        const input = document.getElementById('files-upload-input');
        input.addEventListener('change', filesUploadSelected);

        _filesCurrentPath = "";
        _filesEditorPath = null;
        _filesEditorDirty = false;
        await filesLoad("");
    },
});

async function filesLoad(path) {
    _filesCurrentPath = path;
    const list = document.getElementById('files-list');
    const editor = document.getElementById('files-editor');
    editor.style.display = 'none';
    list.style.display = 'block';
    list.innerHTML = '<div class="empty">Загрузка...</div>';

    try {
        const data = await api('/api/files?path=' + encodeURIComponent(path));
        _filesItems = data.items;
        renderFilesBreadcrumb(data.path, data.parent);
        renderFilesList();
    } catch (e) {
        list.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

function filesReload() {
    if (_filesEditorDirty && !confirm('Есть несохранённые изменения. Продолжить?')) {
        return;
    }
    _filesEditorDirty = false;
    filesLoad(_filesCurrentPath);
}

function renderFilesBreadcrumb(path, parent) {
    const bc = document.getElementById('files-breadcrumb');
    const parts = (path || "").split('/').filter(Boolean);
    let html = `<a href="#" onclick="filesGo(''); return false;">🏠 Проект</a>`;
    let acc = "";
    for (const p of parts) {
        acc = acc ? acc + '/' + p : p;
        html += ` / <a href="#" onclick="filesGo('${escapeHtml(acc)}'); return false;">${escapeHtml(p)}</a>`;
    }
    bc.innerHTML = html;
}

function filesGo(path) {
    if (_filesEditorDirty && !confirm('Есть несохранённые изменения. Продолжить?')) {
        return;
    }
    _filesEditorDirty = false;
    filesLoad(path);
}

function filesFormatSize(n) {
    if (!n) return '';
    if (n < 1024) return n + ' Б';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' КБ';
    return (n / 1024 / 1024).toFixed(1) + ' МБ';
}

function renderFilesList() {
    const list = document.getElementById('files-list');
    if (!_filesItems.length) {
        list.innerHTML = '<div class="empty">Папка пуста</div>';
        return;
    }
    const icons = { dir: '📁', text: '📄', image: '🖼', file: '📦' };
    list.innerHTML = _filesItems.map(it => `
        <div class="files-row" data-path="${escapeHtml(it.path)}" data-kind="${it.kind}">
            <span class="files-icon">${icons[it.kind] || '📦'}</span>
            <span class="files-name">${escapeHtml(it.name)}</span>
            <span class="files-size">${it.kind === 'dir' ? '' : filesFormatSize(it.size)}</span>
            <span class="files-modified">${escapeHtml(it.modified)}</span>
            <span class="files-row-actions">
                ${it.kind !== 'dir' ? `<a class="files-action" href="/api/files/raw?path=${encodeURIComponent(it.path)}" download title="Скачать">⬇</a>` : ''}
                <button class="files-action" onclick="filesRename('${escapeHtml(it.path)}', '${escapeHtml(it.name)}', event)" title="Переименовать">✏️</button>
                <button class="files-action danger" onclick="filesDelete('${escapeHtml(it.path)}', '${escapeHtml(it.name)}', event)" title="Удалить">🗑</button>
            </span>
        </div>
    `).join('');

    list.querySelectorAll('.files-row').forEach(row => {
        const kind = row.dataset.kind;
        const path = row.dataset.path;
        row.addEventListener('click', (e) => {
            if (e.target.closest('.files-row-actions')) return;
            if (kind === 'dir') {
                filesGo(path);
            } else if (kind === 'text') {
                filesOpenEditor(path);
            }
        });
    });
}

async function filesOpenEditor(path) {
    if (_filesEditorDirty && !confirm('Есть несохранённые изменения. Продолжить?')) return;
    _filesEditorDirty = false;
    const list = document.getElementById('files-list');
    const editor = document.getElementById('files-editor');
    list.style.display = 'none';
    editor.style.display = 'flex';

    try {
        const data = await api('/api/files/read?path=' + encodeURIComponent(path));
        _filesEditorPath = path;
        editor.innerHTML = `
            <div class="files-editor-head">
                <div><b>${escapeHtml(path)}</b>
                     <span class="files-editor-meta">${filesFormatSize(data.size)}</span></div>
                <div class="files-editor-actions">
                    <button class="btn small" onclick="filesCloseEditor()">← Назад</button>
                    <button class="btn small primary" id="files-save-btn"
                            onclick="filesSaveEditor()">💾 Сохранить</button>
                </div>
            </div>
            <textarea id="files-editor-text" spellcheck="false"></textarea>
        `;
        const ta = document.getElementById('files-editor-text');
        ta.value = data.content;
        ta.addEventListener('input', () => {
            _filesEditorDirty = true;
            const btn = document.getElementById('files-save-btn');
            if (btn) btn.textContent = '💾 Сохранить *';
        });
        ta.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
                e.preventDefault();
                filesSaveEditor();
            }
        });
    } catch (e) {
        editor.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

function filesCloseEditor() {
    if (_filesEditorDirty && !confirm('Есть несохранённые изменения. Выйти без сохранения?')) {
        return;
    }
    _filesEditorDirty = false;
    _filesEditorPath = null;
    document.getElementById('files-editor').style.display = 'none';
    document.getElementById('files-list').style.display = 'block';
}

async function filesSaveEditor() {
    if (!_filesEditorPath) return;
    const ta = document.getElementById('files-editor-text');
    const btn = document.getElementById('files-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = '💾 Сохранение...'; }
    try {
        await api('/api/files/write?path=' + encodeURIComponent(_filesEditorPath), {
            method: 'PUT',
            body: JSON.stringify({ content: ta.value }),
        });
        _filesEditorDirty = false;
        if (btn) btn.textContent = '✅ Сохранено';
        setTimeout(() => { if (btn) btn.textContent = '💾 Сохранить'; }, 1500);
    } catch (e) {
        alert('Ошибка: ' + e.message);
        if (btn) { btn.disabled = false; btn.textContent = '💾 Сохранить'; }
    }
}

async function filesUploadSelected() {
    const input = document.getElementById('files-upload-input');
    const files = Array.from(input.files || []);
    input.value = '';
    if (!files.length) return;

    for (const f of files) {
        const fd = new FormData();
        fd.append('file', f);
        try {
            const r = await fetch('/api/files/upload?path=' +
                    encodeURIComponent(_filesCurrentPath), {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-CSRF-Token': getCsrfToken() },
                body: fd,
            });
            if (!r.ok) {
                alert(`Ошибка загрузки ${f.name}: ${await r.text()}`);
                return;
            }
        } catch (e) {
            alert(`Ошибка сети при загрузке ${f.name}: ${e.message}`);
            return;
        }
    }
    await filesLoad(_filesCurrentPath);
}

async function filesPromptMkdir() {
    const name = prompt('Имя новой папки:');
    if (!name) return;
    try {
        await api('/api/files/mkdir?path=' + encodeURIComponent(_filesCurrentPath), {
            method: 'POST',
            body: JSON.stringify({ name }),
        });
        await filesLoad(_filesCurrentPath);
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function filesRename(path, oldName, event) {
    if (event) event.stopPropagation();
    const newName = prompt('Новое имя:', oldName);
    if (!newName || newName === oldName) return;
    try {
        await api('/api/files/rename?path=' + encodeURIComponent(path), {
            method: 'POST',
            body: JSON.stringify({ new_name: newName }),
        });
        await filesLoad(_filesCurrentPath);
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function filesDelete(path, name, event) {
    if (event) event.stopPropagation();
    if (!confirm(`Удалить "${name}"? Это необратимо.`)) return;
    try {
        await api('/api/files?path=' + encodeURIComponent(path), { method: 'DELETE' });
        await filesLoad(_filesCurrentPath);
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}