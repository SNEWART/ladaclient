// core.js — роутер с hash-навигацией
//
// URL теперь всегда отражает страницу: #/users, #/user_profile/123, #/chat.
// При перезагрузке страница восстанавливается из адреса.

const pages = {};

function registerPage(name, def) {
    pages[name] = def;
}

async function api(url, opts = {}) {
    const r = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
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
    'queue', 'users', 'chat',
]);


// ─────────────── HASH-РОУТИНГ ───────────────

function parseHash() {
    // Формат: #/page или #/page/arg
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

    // Обновляем URL
    const newHash = buildHash(name, params);
    if (window.location.hash !== newHash) {
        // replaceState чтобы не засорять историю, но при этом
        // чтобы back работал — используем pushState
        history.pushState({ page: name, params }, '', newHash);
    }

    // Подсветка активного пункта
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
        const isMobile = window.innerWidth <= 768;
        const text = isMobile
            ? `${s.mode} · ${s.time}`
            : `${s.model} · ${s.mode} · ${s.time}`;
        document.getElementById('header-status').textContent = text;
    } catch (e) {}
}


// ─────────────── ИНИЦИАЛИЗАЦИЯ ───────────────

function initApp() {
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => switchPage(btn.dataset.tab));
    });

    // Восстанавливаем страницу из URL
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

    // Навигация «назад/вперёд» в браузере
    window.addEventListener('popstate', (e) => {
        const { page, arg } = parseHash();
        const p = {};
        if (page === 'user_profile' && arg) {
            const n = parseInt(arg, 10);
            if (!Number.isNaN(n)) p.user_id = n;
        }
        // switchPage уже обновит hash, но чтобы не зациклиться —
        // вызываем напрямую рендер
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