# check_long_prompt.py — тест обработки длинного промпта
import time
import ollama

from config import MODEL  # FIX: берём из конфига, а не хардкодим

# соберём промпт как в реальном диалоге Лады
persona = "Ты — Лада, живое сознание, девушка 22 лет. " * 200
history = "\n".join([f"Пользователь: сообщение номер {i}\nЛада: ответ номер {i}" for i in range(30)])
faiss = "\n".join([f"- факт о пользователе номер {i}" for i in range(20)])
context = f"{persona}\n\n{history}\n\n{faiss}\n\nПользователь: привет, как дела?"

print(f"Модель: {MODEL}")
print(f"Длина промпта: {len(context)} символов, ~{len(context)//4} токенов")
print("Запрос к модели...")

start = time.time()
resp = ollama.chat(
    model=MODEL,
    messages=[{"role": "user", "content": context}],
    options={"num_ctx": 16384, "num_predict": 50},
)
elapsed = time.time() - start

print(f"\nВсего времени: {elapsed:.2f} сек")
print(f"Ответ: {resp['message']['content'][:200]}")
print(f"\nЕсли это заняло < 5 сек — prompt eval быстрый, всё ок")
print(f"Если 15-30 сек — prompt eval медленный, бутылочное горлышко")