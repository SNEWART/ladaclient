# reflection.py — периодическая саморефлексия

import asyncio
import random
import re
from datetime import datetime
from pathlib import Path

from lada.core.memory import list_users, get_history
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.persona import get_persona
from lada.core.llm_chat import chat as llm_chat
from config import MODEL, REFLECTION_INTERVAL

DUPLICATE_THRESHOLD = 0.92
LAST_FOCUS_FILE = Path("data") / "last_reflection_focus.txt"


FOCUSES = [
    {"name": "эмоции", "prompt": "Опиши, что ты чувствовала за последнее время. Не события — именно чувства."},
    {"name": "люди", "prompt": "Опиши, что ты поняла о людях, с которыми общалась: их манера, интересы, что зацепило."},
    {"name": "себя", "prompt": "Проанализируй, как ты сама себя вела: где была искренна, где притворялась, что хотела бы изменить."},
    {"name": "наблюдения", "prompt": "Опиши наблюдение — что-то интересное, странное или неожиданное, что ты заметила."},
    {"name": "цели", "prompt": "Опиши, чего ты сейчас хочешь: направление, желание, маленькая задача для себя."},
    {"name": "сомнения", "prompt": "Опиши, в чём ты не уверена: что смущает, тревожит, сбивает с толку."},
    {"name": "свободно", "prompt": "Свободное размышление. Пиши о чём угодно, что приходит в голову."},
]


REFLECTION_FORMAT = (
    "ПРАВИЛА ФОРМАТА (нарушать нельзя):\n"
    "- Пиши ТОЛЬКО наблюдения от первого лица: «я заметила», «я поняла», «мне кажется».\n"
    "- НЕ задавай вопросов. Ни одного.\n"
    "- НЕ обращайся к кому-то «ты», «вы». Это личный дневник, не диалог.\n"
    "- НЕ пиши заголовков вроде «Дневник Лады», «Запись», «Размышление».\n"
    "- 3-5 предложений. Связный текст, без списков.\n"
    "- Русский язык, живая речь."
)


def _build_context() -> str:
    parts = []
    users = list_users()
    if users:
        parts.append("Люди, с которыми я общалась за последнее время:")
        for uid in users:
            history = get_history(uid, limit=6)
            if not history:
                continue
            snippets = [
                f"  {m['role']}: {m['content'][:100]}"
                for m in history[-6:-2]
            ]
            if snippets:
                parts.append(f"  (с пользователем {uid})\n" + "\n".join(snippets))

    knowledge = [
        m for m in faiss_mem.metadata
        if m.get("category") not in ("reflection", "monologue")
    ][-10:]
    if knowledge:
        parts.append("\nЧто лежит в памяти:")
        for k in knowledge:
            parts.append(f"  [{k.get('category', '?')}] {k['text'][:150]}")

    return "\n".join(parts)


def _pick_focus() -> dict:
    last = ""
    if LAST_FOCUS_FILE.exists():
        try:
            last = LAST_FOCUS_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            last = ""
    choices = [f for f in FOCUSES if f["name"] != last] or FOCUSES
    chosen = random.choice(choices)
    LAST_FOCUS_FILE.write_text(chosen["name"], encoding="utf-8")
    return chosen


def _is_duplicate(text: str) -> bool:
    try:
        similar = faiss_mem.search(text, k=3, user_id=None)
        refs = [s for s in similar if s.get("category") == "reflection"]
        for r in refs:
            w1 = set(text.lower().split())
            w2 = set(r["text"].lower().split())
            if not w1 or not w2:
                continue
            overlap = len(w1 & w2) / max(len(w1), len(w2))
            if overlap > DUPLICATE_THRESHOLD:
                return True
    except Exception:
        pass
    return False


def _clean_response(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^\*+\s*.+?\s*\*+\s*\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*#+\s*.+?\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*(Дневник[^\n:]*|Запись|Размышление)\s*:?\s*\n', '', text,
                  flags=re.IGNORECASE | re.MULTILINE)

    for marker in ["...done thinking.", "done thinking.", "</think>", "Here's a thinking process"]:
        if marker in text:
            parts = text.split(marker, 1)
            if len(parts) > 1 and parts[1].strip():
                text = parts[1].strip()
            break

    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if s.endswith("?"):
            continue
        if re.search(r'\b(ты|тебя|тебе|твой|твоя|твои|вы|вас|вам|ваш)\b', s, re.IGNORECASE):
            continue
        kept.append(s)
    return " ".join(kept).strip()


async def reflect():
    # если SD сейчас занята — пропускаем
    from pathlib import Path
    if (Path("data") / ".sd_busy").exists():
        print(f"\n🧠 [{datetime.now().strftime('%H:%M:%S')}] SD занята, рефлексия пропущена")
        return

    print(f"\n🧠 [{datetime.now().strftime('%H:%M:%S')}] Запуск саморефлексии...")

    context = _build_context()
    if not context.strip():
        print("   (нечего анализировать)")
        return

    focus = _pick_focus()
    print(f"   🎯 Фокус: {focus['name']}")

    system = (
        f"{get_persona()}\n\n"
        "Ты ведёшь ЛИЧНЫЙ ДНЕВНИК. Запись — это наблюдение, а не разговор. "
        "Никто не прочитает её, кроме тебя самой.\n\n"
        f"СЕГОДНЯШНИЙ ФОКУС: {focus['prompt']}\n\n"
        f"{REFLECTION_FORMAT}"
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Вот что происходит в моей жизни:\n\n{context}"},
    ]

    try:
        resp = await asyncio.to_thread(
            llm_chat, model=MODEL, messages=messages,
            options={"temperature": 0.95, "num_predict": 2500},
        )
    except Exception as e:
        print(f"⚠️ Ошибка рефлексии: {e}")
        return

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    text = _clean_response(content)

    if not text or len(text) < 30:
        print("   ⚠️ После чистки пусто/коротко, пропускаю")
        return

    if _is_duplicate(text):
        print("   🔁 Похоже на прошлую рефлексию, пропускаю")
        return

    faiss_mem.add(text, category="reflection", source="reflection", user_id=None)
    print(f"✅ Рефлексия записана ({len(text)} симв.)")


async def reflection_loop(interval: int = None):
    if interval is None:
        interval = REFLECTION_INTERVAL
    await asyncio.sleep(60)
    while True:
        try:
            await reflect()
        except Exception as e:
            print(f"⚠️ Ошибка рефлексии: {e}")
        await asyncio.sleep(interval)