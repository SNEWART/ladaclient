# check_api.py — проверка всех эндпоинтов веб-панели

import requests

BASE = "http://localhost:8080"
ENDPOINTS = [
    "/api/status",
    "/api/soul",
    "/api/thoughts",
    "/api/reflections",
    "/api/queue",
    "/api/dialogs",
    "/api/users_names",
    "/api/users/list",
    "/api/trust",
    "/api/trust/levels",
    "/api/chat/history",
    # FIX: убраны /api/experience, /api/memories — их нет в web.py
    # FIX: добавлены /api/observations — теперь реализован
    "/api/observations",
]

for ep in ENDPOINTS:
    try:
        r = requests.get(BASE + ep, timeout=10)
        text = r.text
        print(f"\n=== {ep} ({r.status_code}) ===")
        print(text[:400])
    except Exception as e:
        print(f"\n=== {ep} === ОШИБКА: {e}")