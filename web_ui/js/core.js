// core.js — роутер, CSRF, авторизация

const pages = {};

function registerPage(name, def) {
    pages[name] = def;
}

function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
}

function getCsrfToken() {
    return getCookie('lada_csrf');
}

let _authEnabled = false;

async function api(url, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (method !== 'GET' && method !== 'HEAD') {
        const csrf = getCsrfToken();
        if (csrf) headers['X-CSRF-Token'] = csrf;
    }
    const r = await fetch(url, { ...opts, headers, credentials: 'same-origin' });
    if (r.status === 401) {
        renderLogin();
        throw new Error('unauthorized');
    }
    if (!r.ok) throw new Error(await r.text());
    return r.json();
}

function escapeHtml(s) {
    return String(s)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
}

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    sidebar.classList.toggle('open');
    overlay.classList.toggle('open');
}

function closeSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
}

const MAIN_TABS = new Set([
    'dashboard', 'soul', 'thoughts', 'reflections',
    'images', 'queue', 'users', 'persona', 'chat',
]);


// ─────────────── HASH-РОУТИНГ ───────────────

function parseHash() {
    const raw = (window.location.hash || '').replace(/^#\/?/, '');
    if (!raw) return { page: 'dashboard', arg: null };
    const parts = raw.split('/');
    return { page: parts[0] || 'dashboard', arg: parts[1] || null };
}

function buildHash(page, params) {
    let h = '#/' + page;
    if (params && params.user_id !== undefined && params.user_id !== null) {
        h += '/' + params.user_id;
    }
    return h;
}

async function switchPage(name, params = {}) {
    params = params || {};

    const newHash = buildHash(name, params);
    if (window.location.hash !== newHash) {
        history.pushState({ page: name, params }, '', newHash);
    }

    if (MAIN_TABS.has(name)) {
        document.querySelectorAll('.nav-btn').forEach(b =>
            b.classList.toggle('active', b.dataset.tab === name)
        );
    }

    const title = pages[name]?.title || name;
    document.getElementById('tab-title').textContent = title;

    if (window.innerWidth <= 768) {
        closeSidebar();
    }

    const content = document.getElementById('content');
    content.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'tab';
    el.id = 'tab-' + name;
    content.appendChild(el);

    try {
        await pages[name].mount(el, params);
    } catch (e) {
        el.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}


// ─────────────── HEADER / QUEUE ───────────────

async function updateQueueBadge() {
    try {
        const q = await api('/api/queue');
        const badge = document.getElementById('queue-badge');
        if (!badge) return;
        if (q.size > 0) {
            badge.textContent = q.size;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    } catch (e) {}
}

async function refreshHeaderStatus() {
    try {
        const s = await api('/api/status');
        const el = document.getElementById('header-status');
        if (!el) return;
        const isMobile = window.innerWidth <= 768;
        el.textContent = isMobile
            ? `${s.mode} · ${s.time}`
            : `${s.model} · ${s.mode} · ${s.time}`;
    } catch (e) {}
}


// ─────────────── LOGOUT ───────────────

async function doLogout() {
    try {
        await api('/api/auth/logout', { method: 'POST' });
    } catch (e) {}
    location.reload();
}


// ─────────────── INIT ───────────────

async function initApp() {
    // 1. Проверяем сессию
    try {
        const r = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (r.status === 401) {
            renderLogin();
            return;
        }
        const me = await r.json();
        _authEnabled = !!me.auth_enabled;
    } catch (e) {
        // сеть упала — пробуем дальше
    }

    // 2. Кнопка logout в сайдбаре, если авторизация включена
    if (_authEnabled) {
        const sidebar = document.getElementById('sidebar');
        if (sidebar && !document.getElementById('logout-btn')) {
            const btn = document.createElement('button');
            btn.className = 'nav-btn';
            btn.id = 'logout-btn';
            btn.textContent = '🚪 Выйти';
            btn.style.marginTop = 'auto';
            btn.onclick = doLogout;
            sidebar.appendChild(btn);
        }
    }

    // 3. Обычная инициализация
    document.querySelectorAll('.nav-btn').forEach(btn => {
        if (btn.id === 'logout-btn') return;
        btn.addEventListener('click', () => switchPage(btn.dataset.tab));
    });

    const { page, arg } = parseHash();
    const params = {};
    if (page === 'user_profile' && arg) {
        const n = parseInt(arg, 10);
        if (!Number.isNaN(n)) params.user_id = n;
    }

    switchPage(page, params);

    refreshHeaderStatus();
    updateQueueBadge();
    setInterval(refreshHeaderStatus, 5000);
    setInterval(updateQueueBadge, 5000);

    window.addEventListener('resize', refreshHeaderStatus);

    window.addEventListener('popstate', () => {
        const { page, arg } = parseHash();
        const p = {};
        if (page === 'user_profile' && arg) {
            const n = parseInt(arg, 10);
            if (!Number.isNaN(n)) p.user_id = n;
        }
        if (MAIN_TABS.has(page)) {
            document.querySelectorAll('.nav-btn').forEach(b =>
                b.classList.toggle('active', b.dataset.tab === page)
            );
        }
        document.getElementById('tab-title').textContent = pages[page]?.title || page;
        const content = document.getElementById('content');
        content.innerHTML = '';
        const el = document.createElement('div');
        el.className = 'tab';
        el.id = 'tab-' + page;
        content.appendChild(el);
        try {
            pages[page].mount(el, p);
        } catch (err) {
            el.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(err.message)}</div>`;
        }
    });
}