// login.js — экран входа

function renderLogin() {
    document.body.innerHTML = `
        <div class="login-screen">
            <form class="login-box" onsubmit="doLogin(event)">
                <div class="login-logo">🧠 <span>Лада</span></div>
                <input type="text" id="login-user" placeholder="Логин"
                       autocomplete="username" required>
                <input type="password" id="login-pass" placeholder="Пароль"
                       autocomplete="current-password" required>
                <button type="submit" id="login-btn">Войти</button>
                <div class="login-error" id="login-error"></div>
            </form>
        </div>
    `;
    document.body.style.display = 'block';
    const u = document.getElementById('login-user');
    if (u) u.focus();
}

async function doLogin(e) {
    e.preventDefault();
    const btn = document.getElementById('login-btn');
    const err = document.getElementById('login-error');
    btn.disabled = true;
    err.textContent = '';
    const username = document.getElementById('login-user').value.trim();
    const password = document.getElementById('login-pass').value;
    try {
        const r = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ username, password }),
        });
        if (!r.ok) {
            err.textContent = 'Неверный логин или пароль';
            btn.disabled = false;
            return;
        }
        location.reload();
    } catch (e) {
        err.textContent = 'Ошибка сети';
        btn.disabled = false;
    }
}