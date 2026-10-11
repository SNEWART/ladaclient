# hash_password.py — генерирует pbkdf2-хеш пароля для .env
# Запуск: python hash_password.py
import hashlib
import secrets

pwd = input("Пароль: ").strip()
if not pwd:
    print("Пустой пароль")
    raise SystemExit(1)

salt = secrets.token_bytes(16)
iterations = 200_000
h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, iterations)
print()
print("Скопируй строку ниже в .env как LADA_WEB_PASS_HASH:")
print(f"pbkdf2_sha256${iterations}${salt.hex()}${h.hex()}")
