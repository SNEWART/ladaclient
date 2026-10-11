// ssh.js — SSH-терминал в браузере (xterm.js + WebSocket)

let _sshHosts = [];
let _sshCurrent = null;
let _term = null;
let _fitAddon = null;
let _socket = null;
let _resizeObserver = null;
let _xtermReady = false;

registerPage('ssh', {
    title: 'SSH',
    mount: async (el) => {
        el.innerHTML = `
            <div class="ssh-toolbar">
                <div class="ssh-hosts" id="ssh-hosts"></div>
                <div class="ssh-status" id="ssh-status">—</div>
            </div>
            <div class="ssh-quick" id="ssh-quick"></div>
            <div class="ssh-term-wrap">
                <div id="ssh-term"></div>
            </div>
        `;
        await loadSshHosts();
        initTerminal();
    },
});

async function loadSshHosts() {
    const box = document.getElementById('ssh-hosts');
    if (!box) return;
    try {
        _sshHosts = await api('/api/ssh/hosts');
        if (!_sshHosts.length) {
            box.innerHTML = '<span class="empty-small">Нет хостов. Заполни web_ui/ssh_hosts.json</span>';
            return;
        }
        box.innerHTML = _sshHosts.map(h => `
            <button class="ssh-host-btn ${h.id === _sshCurrent ? 'active' : ''}"
                    data-host-id="${escapeHtml(h.id)}">
                ${escapeHtml(h.name)}
            </button>
        `).join('');
        box.querySelectorAll('.ssh-host-btn').forEach(btn => {
            btn.addEventListener('click', () => connectHost(btn.dataset.hostId));
        });

        if (!_sshCurrent && _sshHosts.length) {
            connectHost(_sshHosts[0].id);
        }
    } catch (e) {
        box.innerHTML = `<span class="empty-small">Ошибка: ${escapeHtml(e.message)}</span>`;
    }
}

function renderQuickCommands(host) {
    const box = document.getElementById('ssh-quick');
    if (!box) return;
    if (!host || !host.commands || !host.commands.length) {
        box.innerHTML = '';
        return;
    }
    box.innerHTML = host.commands.map(c => `
        <button class="btn small ssh-cmd-btn" data-cmd="${escapeHtml(c.cmd)}">
            ${escapeHtml(c.name)}
        </button>
    `).join('');
    box.querySelectorAll('.ssh-cmd-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            sendToTerminal(btn.dataset.cmd + '\r');
            if (_term) _term.focus();
        });
    });
}

function initTerminal() {
    if (_xtermReady || typeof Terminal === 'undefined') {
        return;
    }
    _xtermReady = true;

    _term = new Terminal({
        cursorBlink: true,
        fontFamily: "'Consolas', 'Monaco', 'Courier New', monospace",
        fontSize: 14,
        theme: {
            background: '#0a0a14',
            foreground: '#f0f0f5',
            cursor: '#a78bfa',
            selectionBackground: '#3a3a55',
        },
        scrollback: 5000,
    });
    _fitAddon = new FitAddon.FitAddon();
    _term.loadAddon(_fitAddon);

    const container = document.getElementById('ssh-term');
    if (!container) return;
    _term.open(container);

    setTimeout(() => {
        try { _fitAddon.fit(); } catch (e) {}
    }, 50);

    _term.onData(data => {
        sendToSocket({ type: 'input', data });
    });

    _resizeObserver = new ResizeObserver(() => {
        try {
            _fitAddon.fit();
            sendResize();
        } catch (e) {}
    });
    _resizeObserver.observe(container);

    _term.onResize(() => {
        sendResize();
    });
}

function setStatus(text, cls) {
    const el = document.getElementById('ssh-status');
    if (!el) return;
    el.textContent = text;
    el.className = 'ssh-status ' + (cls || '');
}

function connectHost(hostId) {
    if (_socket) {
        try { _socket.close(); } catch (e) {}
        _socket = null;
    }
    _sshCurrent = hostId;

    document.querySelectorAll('.ssh-host-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.hostId === hostId)
    );

    const host = _sshHosts.find(h => h.id === hostId);
    renderQuickCommands(host);

    if (_term) {
        _term.reset();
        _term.write(`\x1b[33mПодключаюсь к ${host?.name || hostId}...\x1b[0m\r\n`);
    }
    setStatus('подключение...', '');

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/ssh/terminal?host_id=${encodeURIComponent(hostId)}`;
    _socket = new WebSocket(url);

    _socket.onopen = () => {
        setStatus('подключено', 'ok');
        setTimeout(() => {
            try { _fitAddon.fit(); } catch (e) {}
            sendResize();
            if (_term) _term.focus();
        }, 100);
    };

    _socket.onmessage = (ev) => {
        try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'output') {
                if (_term) _term.write(msg.data);
            } else if (msg.type === 'connected') {
                setStatus(`подключено · ${msg.host}`, 'ok');
            } else if (msg.type === 'error') {
                if (_term) _term.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`);
                setStatus('ошибка', 'err');
            }
        } catch (e) {}
    };

    _socket.onerror = () => {
        setStatus('ошибка соединения', 'err');
    };

    _socket.onclose = (ev) => {
        if (_term) {
            _term.write(`\r\n\x1b[33m[соединение закрыто, код ${ev.code}]\x1b[0m\r\n`);
        }
        if (ev.code === 4401) {
            setStatus('нет авторизации', 'err');
        } else {
            setStatus('отключено', '');
        }
        _socket = null;
    };
}

function sendToSocket(obj) {
    if (_socket && _socket.readyState === WebSocket.OPEN) {
        _socket.send(JSON.stringify(obj));
    }
}

function sendToTerminal(data) {
    sendToSocket({ type: 'input', data });
}

function sendResize() {
    if (!_term) return;
    sendToSocket({
        type: 'resize',
        cols: _term.cols,
        rows: _term.rows,
    });
}