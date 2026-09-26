// core.js — роутер и общие утилиты

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

// ─── Мобильное меню ───
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

async function switchPage(name) {
    document.querySelectorAll('.nav-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === name)
    );
    document.getElementById('tab-title').textContent = pages[name]?.title || name;

    // на мобиле закрываем сайдбар после выбора
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
        await pages[name].mount(el);
    } catch (e) {
        el.innerHTML = `<div class="empty">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

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
        // на мобиле сокращаем строку
        const isMobile = window.innerWidth <= 768;
        const text = isMobile
            ? `${s.mode} · ${s.time}`
            : `${s.model} · ${s.mode} · ${s.time}`;
        document.getElementById('header-status').textContent = text;
    } catch (e) {}
}

function initApp() {
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => switchPage(btn.dataset.tab));
    });
    switchPage('dashboard');
    refreshHeaderStatus();
    updateQueueBadge();
    setInterval(refreshHeaderStatus, 5000);
    setInterval(updateQueueBadge, 5000);

    // при ресайзе обновляем строку статуса
    window.addEventListener('resize', refreshHeaderStatus);
}