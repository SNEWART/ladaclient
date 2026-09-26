# check_api.py — проверка всех эндпоинтов веб-панели

import requests

BASE = "http://localhost:8080"
ENDPOINTS = [
    "/api/status",
    "/api/soul",
    "/api/thoughts",
    "/api/reflections",
    "/api/experience",
    "/api/memories",
    "/api/queue",
    "/api/dialogs",
    "/api/users_names",
]

for ep in ENDPOINTS:
    try:
        r = requests.get(BASE + ep, timeout=10)
        text = r.text
        print(f"\n=== {ep} ({r.status_code}) ===")
        print(text[:400])
    except Exception as e:
        print(f"\n=== {ep} === ОШИБКА: {e}")