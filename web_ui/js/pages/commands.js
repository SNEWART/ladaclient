// commands.js — вкладка ручных команд

let _cmdActions = [];
let _cmdBusy = false;

registerPage('commands', {
    title: 'Команды',
    mount: async (el) => {
        el.innerHTML = `
            <div class="cmd-toolbar">
                <div class="cmd-title">Безопасные операции с проектом</div>
                <button class="btn small" onclick="cmdBind()">🔄 Обновить</button>
            </div>
            <div id="cmd-actions" class="cmd-actions">
                <div class="empty">Загрузка...</div>
            </div>
            <div class="cmd-output-wrap">
                <div class="cmd-output-head">
                    <span id="cmd-output-title">Вывод</span>
                    <button class="btn small" onclick="cmdClear()">Очистить</button>
                </div>
                <pre class="cmd-output" id="cmd-output">(пусто)</pre>
            </div>
        `;
        await cmdBind();
    },
});

async function cmdBind() {
    const box = document.getElementById('cmd-actions');
    if (!box) return;
    try {
        _cmdActions = await api('/api/commands/list');
        box.innerHTML = _cmdActions.map(a => `
            <button class="cmd-action" data-id="${escapeHtml(a.id)}"
                    onclick="cmdRun('${escapeHtml(a.id)}')">
                <div class="cmd-action-name">${escapeHtml(a.name)}</div>
                <div class="cmd-action-desc">${escapeHtml(a.desc || '')}</div>
            </button>
        `).join('');
    } catch (e) {
        box.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

async function cmdRun(actionId) {
    if (_cmdBusy) return;
    _cmdBusy = true;
    document.querySelectorAll('.cmd-action').forEach(b => b.disabled = true);

    const out = document.getElementById('cmd-output');
    const title = document.getElementById('cmd-output-title');
    const act = _cmdActions.find(a => a.id === actionId);
    if (title) title.textContent = `⏳ ${act ? act.name : actionId}...`;
    if (out) {
        out.textContent = `$ ${actionId}\n`;
        out.classList.remove('ok', 'err');
    }

    try {
        const r = await api('/api/commands/run', {
            method: 'POST',
            body: JSON.stringify({ action: actionId }),
        });
        if (out) {
            out.textContent =
                `$ ${r.name || actionId}\n\n` +
                (r.output || '(пусто)') +
                `\n\n[exit=${r.exit_code}, время=${r.duration}с]`;
            out.classList.add(r.ok ? 'ok' : 'err');
        }
        if (title) title.textContent = r.ok ? `✅ ${r.name}` : `❌ ${r.name}`;
    } catch (e) {
        if (out) {
            out.textContent = `❌ Ошибка: ${e.message}`;
            out.classList.add('err');
        }
        if (title) title.textContent = '❌ Ошибка';
    } finally {
        _cmdBusy = false;
        document.querySelectorAll('.cmd-action').forEach(b => b.disabled = false);
    }
}

function cmdClear() {
    const out = document.getElementById('cmd-output');
    const title = document.getElementById('cmd-output-title');
    if (out) {
        out.textContent = '(пусто)';
        out.classList.remove('ok', 'err');
    }
    if (title) title.textContent = 'Вывод';
}