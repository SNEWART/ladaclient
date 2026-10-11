# utils.py — общие утилиты: логи, хелперы, детекторы

import re
from datetime import datetime


def log(text: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", flush=True)


def log_thinking(thinking: str, limit: int = 400):
    if not thinking:
        return
    t = thinking.strip()
    if len(t) > limit:
        t = t[:limit] + "…"
    print(f"   🧠 thinking: {t}", flush=True)


def has_asterisk_actions(text: str) -> bool:
    return bool(re.search(r'\*[^*\n]{2,60}\*', text))


def asks_for_monologue(text: str) -> bool:
    low = text.lower()
    markers = [
        "расскажи сказку", "расскажи историю", "расскажи что-нибудь",
        "расскажи мне", "продолжай без меня", "продолжи без меня",
        "говори сам", "говори сама", "монолог", "без меня продолжай",
        "не задавай вопросов", "просто расскажи", "развлеки меня",
    ]
    return any(m in low for m in markers)


YES_MARKERS = [
    "да", "конечно", "согласен", "согласна", "хочу", "буду",
    "ок", "окей", "давай", "естественно", "ага", "угу",
    "да, буду", "да, хочу", "да, согласен", "да, согласна",
]

NO_MARKERS = [
    "нет", "не хочу", "не готов", "не готова", "не сейчас",
    "извини, но нет", "прости, нет", "не могу", "не буду",
    "нет, спасибо", "не для меня", "откажусь", "не надо",
]


def detect_yes_no(text: str):
    if not text:
        return None
    low = text.lower().strip()
    for m in NO_MARKERS:
        if m in low:
            return "no"
    for m in YES_MARKERS:
        if low == m or low.startswith(m + " ") or low.startswith(m + ",") \
                or low.startswith(m + "!") or low.startswith(m + "."):
            return "yes"
    words = low.split()
    if len(words) <= 3:
        for m in YES_MARKERS:
            if m in words:
                return "yes"
        for m in NO_MARKERS:
            if m in words:
                return "no"
    return None


# ─── Очистка действий в звёздочках ───
#
# Ловим и короткие (*улыбнулась*), и длинные (*я пытаюсь описать её,
# вспоминаю форму, цвет, свет...*). Раньше лимит был 80 символов в
# одну строку — длинные блоки и многострочные не удалялись.
#
# Порядок: сначала многострочные *...* (DOTALL), потом одиночные.

_MULTILINE_ACTION = re.compile(r'\*{1,2}[^*]{5,400}?\*{1,2}', re.DOTALL)
_INLINE_ACTION = re.compile(r'\*[^*\n]{1,200}\*')
_DOUBLE_STAR = re.compile(r'\*\*[^*\n]{1,200}\*\*')


def strip_asterisk_actions(text: str) -> tuple:
    if not text:
        return text, False
    before = text

    # Сначала **bold** — но только если это внутри текста, не отдельное
    # слово: *** и **** редки в живой речи, но **...** бывает как
    # «действие». Если это чистое **слово** без пробелов — оставим.
    text = _DOUBLE_STAR.sub(
        lambda m: m.group(0)[2:-2]
        if " " in m.group(0)[2:-2] else m.group(0),
        text,
    )

    # Одиночные *действия* — включая многострочные.
    text = _INLINE_ACTION.sub('', text)
    text = _MULTILINE_ACTION.sub('', text)

    # Чистим повисшие пробелы и пустые строки.
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'^\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text, (text != before)


# ─── Очистка «псевдо-пометок», которые модель имитирует ───
#
# Модель видит в истории "[отправила фото: ...]" и начинает писать
# похожее. Формат не наш — вырезаем молча.

_FAKE_NOTE = re.compile(
    r'\[\s*отправил[аи]?\s+(?:фото|стикер|картинку|изображение)'
    r'(?::[^\]]*)?\]\s*',
    re.IGNORECASE,
)
_FAKE_NOTE_2 = re.compile(
    r'\[\s*(?:фото|стикер)\s+отправлен[оа]?\s*\]\s*',
    re.IGNORECASE,
)
_FAKE_NOTE_3 = re.compile(
    r'\[\s*сгенерировал[аи]?\s+фото[^\]]*\]\s*',
    re.IGNORECASE,
)


def strip_fake_notes(text: str) -> tuple:
    if not text:
        return text, False
    before = text
    text = _FAKE_NOTE.sub('', text)
    text = _FAKE_NOTE_2.sub('', text)
    text = _FAKE_NOTE_3.sub('', text)
    text = re.sub(r'^\s+', '', text, flags=re.MULTILINE).strip()
    return text, (text != before)


def strip_question_echo(reply: str, user_msg: str) -> str:
    if not reply or not user_msg:
        return reply

    reply = reply.strip()
    lines = reply.split("\n", 1)
    first_line = lines[0].strip()

    if len(first_line) > 45 or not first_line.endswith("?"):
        return reply

    user_tokens = set(re.findall(r'\w{2,}', user_msg.lower()))
    first_tokens = set(re.findall(r'\w{2,}', first_line.lower()))

    if not (user_tokens & first_tokens):
        return reply

    rest = lines[1].strip() if len(lines) > 1 else ""
    if rest:
        return rest
    return reply