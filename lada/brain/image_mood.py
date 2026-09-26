# image_mood.py — сборка промпта сцены.
#
# КЛЮЧЕВОЕ: outfit и location выбираются по УРОВНЮ СЦЕНЫ (что в hint),
# а не по уровню пользователя. Уровень пользователя — это ПОТОЛОК.
#
# Пример:
#   Пользователь с 9/9, hint "sitting by window, coffee" → level 0 → обычная одежда
#   Пользователь с 9/9, hint "lingerie, bedroom" → level 7 → бельё
#   Пользователь с 4/9, hint "lingerie, bedroom" → level 7 → обрезано до 4 → обычная
#
# Так nude появляется только если hint явно описывает nude-сцену.

import random
from lada.brain import image_policy


# ─────────────────────────────────────────────
# ВЫРАЖЕНИЕ ЛИЦА ПО НАСТРОЕНИЮ
# ─────────────────────────────────────────────
MOOD_EXPRESSION = {
    "спокойная": [
        "relaxed expression, soft gaze",
        "gentle half-smile, calm eyes",
        "serene face, eyes slightly narrowed",
        "quiet smile, no tension",
        "peaceful look, natural face",
        "composed expression, soft features",
    ],
    "весёлая": [
        "bright smile, lively eyes",
        "wide grin, sparkling eyes",
        "cheerful expression, raised cheeks",
        "laughing, eyes crinkled",
        "joyful face, open smile",
        "happy expression, bright eyes",
    ],
    "задумчивая": [
        "thoughtful gaze, distant look",
        "pensive expression, soft eyes",
        "reflective mood, lips slightly pressed",
        "looking away, quiet face",
        "contemplative look, still features",
        "deep in thought, calm expression",
    ],
    "раздражённая": [
        "cold gaze, pressed lips",
        "annoyed expression, narrowed eyes",
        "closed-off face, no smile",
        "tense jaw, sharp look",
        "irritated expression, flat mouth",
        "distant stare, minimal expression",
    ],
    "уставшая": [
        "tired eyes, no makeup",
        "sleepy gaze, half-lidded eyes",
        "soft exhausted expression",
        "expressionless, drained look",
        "tired but peaceful face",
        "drowsy, soft focus, heavy eyelids",
    ],
    "игривая": [
        "playful smirk, eye contact",
        "mischievous glint, half-smile",
        "raised eyebrow, cheeky look",
        "biting lip, teasing expression",
        "wide grin, lively eyes",
        "cheeky face, playful look",
    ],
    "холодная": [
        "distant look, neutral face",
        "cold expression, no smile",
        "closed lips, sharp gaze",
        "indifferent look, minimal emotion",
        "unreadable face, still features",
        "detached gaze, flat expression",
    ],
    "тёплая": [
        "soft smile, warm eyes",
        "gentle expression, kind gaze",
        "warm look, slight smile",
        "tender expression, relaxed features",
        "friendly face, soft eyes",
        "cozy look, peaceful smile",
    ],
    "расстроенная": [
        "downcast eyes, no smile",
        "sad expression, wet gaze",
        "upset look, lowered head",
        "fragile expression, quiet face",
        "melancholic look, still features",
        "hurt eyes, pressed lips",
    ],
    "вдохновлённая": [
        "bright eyes, curious look",
        "eager expression, focused gaze",
        "inspired face, alert eyes",
        "open look, animated features",
        "interested expression, light in eyes",
        "creative mood, thoughtful but alive",
    ],
    "нейтральная": [
        "natural expression, calm face",
        "neutral look, soft eyes",
        "relaxed face, no strong emotion",
        "ordinary expression, present look",
        "quiet gaze, composed features",
        "soft neutral expression",
    ],
}


# ─────────────────────────────────────────────
# АТМОСФЕРА / СВЕТ ПО НАСТРОЕНИЮ
# ─────────────────────────────────────────────
MOOD_ATMOSPHERE = {
    "спокойная": [
        "soft daylight, muted palette, calm interior",
        "warm lamp light, peaceful atmosphere, gentle shadows",
        "overcast light, quiet room, low contrast",
        "evening ambience, soft glow, minimal surroundings",
        "cool morning light, tidy space, stillness",
        "muted tones, soft focus background",
    ],
    "весёлая": [
        "bright warm light, lively interior",
        "sunlit room, high contrast, cheerful tone",
        "afternoon sun, warm tones, airy space",
        "warm cafe light, bokeh background",
        "golden hour glow, cozy home",
        "vibrant colors, soft shadows",
    ],
    "задумчивая": [
        "soft window light, quiet room",
        "rain outside, muted interior",
        "overcast daylight, cool palette",
        "dusk light, soft shadows, still air",
        "warm dim light, introspective mood",
        "cloudy sky, diffused light",
    ],
    "раздражённая": [
        "cool light, minimal interior",
        "grey overcast, sharp shadows",
        "dim indoor light, no warmth",
        "flat daylight, empty room",
        "cold tones, sparse surroundings",
        "hard light, high contrast",
    ],
    "уставшая": [
        "dim light, late evening, quiet room",
        "soft bedside lamp, muted tones, no clutter",
        "overcast daylight, muted palette, still air",
        "low warm light, cozy but heavy atmosphere",
        "night lamp glow, soft shadows",
        "drowsy warm light, minimal detail",
    ],
    "игривая": [
        "bright warm light, lively interior",
        "sunlit room, high contrast, playful tone",
        "evening warm glow, cozy but electric",
        "warm cafe light, bokeh background",
        "vibrant light, pops of color",
        "soft afternoon sun, dynamic mood",
    ],
    "холодная": [
        "grey light, minimal palette",
        "hard shadows, empty space",
        "winter daylight, cool tones",
        "flat indoor light, no warmth",
        "clinical bright light, neutral room",
        "distant light source, muted",
    ],
    "тёплая": [
        "warm lamp light, cozy home",
        "golden hour, soft warm tones",
        "evening candlelight, warm shadows",
        "cozy indoors, warm wood tones",
        "soft daylight through curtain, warm",
        "autumn afternoon light, cozy",
    ],
    "расстроенная": [
        "dim light, quiet room",
        "rain outside, blurred interior",
        "cool overcast, muted palette",
        "soft shadow, empty space",
        "evening gloom, soft grey light",
        "subdued tones, still air",
    ],
    "вдохновлённая": [
        "bright morning light, open space",
        "studio light, clean background",
        "sunlit room, clear view",
        "window light, fresh mood",
        "high key light, airy feel",
        "creative space, warm daylight",
    ],
    "нейтральная": [
        "soft daylight, neutral interior",
        "ordinary room, natural light",
        "evening ambient light, calm",
        "daylight through window, soft shadows",
        "warm indoor light, simple setting",
        "overcast light, muted colors",
    ],
}


# ─────────────────────────────────────────────
# НЕГАТИВНЫЕ ТЕГИ ПО НАСТРОЕНИЮ
# ─────────────────────────────────────────────
MOOD_NEGATIVE = {
    "спокойная": "tense, exaggerated, hyperactive, chaotic, dramatic",
    "весёлая": "gloomy, dim, sad, melancholic, dark",
    "задумчивая": "hyperactive, energetic, party, grin, loud",
    "раздражённая": "smile, flirtatious, warm, cozy, playful",
    "уставшая": "glamour, party, energetic, seductive, heavy makeup",
    "игривая": "cold, distant, gloomy, sad, lifeless",
    "холодная": "warm smile, hugging, playful, cozy, joyful",
    "тёплая": "sharp light, empty room, cold, distant, harsh",
    "расстроенная": "smile, teasing, playful, joyful, bright",
    "вдохновлённая": "tired, dull, gloomy, sad, lifeless",
    "нейтральная": "",
}


# ─────────────────────────────────────────────
# ОБЩИЙ НЕГАТИВ ПО ОДЕЖДЕ
# ─────────────────────────────────────────────
CLOTHING_NEGATIVE = (
    "multicolored clothes, colorful clothes, rainbow clothes, "
    "neon clothes, printed pattern, floral pattern, polka dots, "
    "striped clothes, plaid pattern, camouflage, tie-dye, "
    "brand logos, text on clothes, "
    "pink clothes, red clothes, purple clothes, orange clothes, "
    "yellow clothes, blue clothes, brown clothes"
)


# ─────────────────────────────────────────────
# РАКУРСЫ КАМЕРЫ
# ─────────────────────────────────────────────
CAMERA_SHOTS = [
    "close-up portrait",
    "medium shot, waist-up",
    "three-quarter view",
    "selfie, handheld",
    "full body shot",
    "shoulder-up shot",
    "environmental portrait, wide",
    "mirror selfie",
]


# ─────────────────────────────────────────────
# ОДЕЖДА ПО УРОВНЮ СЦЕНЫ
# Фирменный цвет: spring green (#00FF7F)
# ─────────────────────────────────────────────
TRUST_OUTFIT = {
    0: "(fully clothed:1.3), (covered body:1.2), spring green t-shirt, blue denim jeans, casual clothes",
    1: "(fully clothed:1.3), (covered body:1.2), spring green hoodie, long sleeves",
    2: "(fully clothed:1.3), (covered body:1.2), spring green oversized shirt, soft cotton",
    3: "spring green loose shirt, long sleeves, covered with soft cream blanket",
    4: "spring green fitted top, short sleeves, fully covered chest",
    5: "spring green tank top, dark grey shorts",
    6: "spring green bikini",
    7: "spring green lace lingerie",
    8: "spring green sheer lingerie, topless",
    9: "completely nude, bare skin, no clothing",
}


# ─────────────────────────────────────────────
# ЛОКАЦИЯ ПО УРОВНЮ СЦЕНЫ
# ─────────────────────────────────────────────
TRUST_LOCATION = {
    0: "neutral indoor setting",
    1: "cozy home setting",
    2: "relaxed home atmosphere",
    3: "cozy sofa or bed, soft light, blanket nearby",
    4: "casual indoor, soft light",
    5: "athletic or casual setting",
    6: "pool or beach setting",
    7: "indoor intimate setting",
    8: "bedroom, warm light, soft focus",
    9: "bedroom, intimate lighting, soft focus",
}


# ─────────────────────────────────────────────
# НЕГАТИВНЫЕ ТЕГИ ПО УРОВНЮ (для негативного промпта)
# ─────────────────────────────────────────────
_HARD_NSFW_NEGATIVE = (
    "(nude:1.6), (naked:1.6), (topless:1.6), (bottomless:1.5), "
    "(nipples:1.5), (areola:1.5), (exposed breasts:1.5), (breasts out:1.5), "
    "(cleavage:1.3), (deep cleavage:1.4), (see-through:1.4), "
    "(transparent clothing:1.4), (lingerie:1.3), (underwear:1.3), "
    "(bra visible:1.3), (panties visible:1.3), (nsfw:1.5), (explicit:1.5), "
    "(sex:1.5), (sexual:1.4), (spread legs:1.3), (suggestive pose:1.3), "
    "(seductive:1.2), (undressed:1.5), (unclothed:1.5), "
    "(bare breasts:1.5), (bare chest:1.5), (bare skin:1.2)"
)

TRUST_NEGATIVE = {
    0: _HARD_NSFW_NEGATIVE,
    1: _HARD_NSFW_NEGATIVE,
    2: _HARD_NSFW_NEGATIVE,
    3: _HARD_NSFW_NEGATIVE,
    4: _HARD_NSFW_NEGATIVE,
    5: (
        "(nude:1.4), (naked:1.4), (topless:1.4), (nipples:1.4), "
        "(areola:1.4), (exposed breasts:1.4), (breasts out:1.4), "
        "(see-through:1.3), (lingerie:1.2), (underwear:1.2), "
        "(nsfw:1.4), (explicit:1.4), (sex:1.4), (spread legs:1.2)"
    ),
    6: (
        "(nude:1.4), (naked:1.4), (topless:1.4), (nipples:1.4), "
        "(areola:1.4), (exposed breasts:1.4), (breasts out:1.4), "
        "(lingerie:1.2), (underwear:1.2), (nsfw:1.4), (explicit:1.4)"
    ),
    7: (
        "(nude:1.3), (naked:1.3), (topless:1.3), (nipples:1.3), "
        "(exposed breasts:1.3), (nsfw:1.3), (explicit:1.3), "
        "casual clothes, jeans, hoodie, sweater"
    ),
    8: (
        "casual clothes, jeans, hoodie, sweater, pants, "
        "(sex:1.3), (sexual:1.3), (explicit:1.3)"
    ),
    9: (
        "clothes, dressed, clothed, jeans, pants, hoodie, sweater, shirt, "
        "lingerie, bra, panties, underwear"
    ),
}


# ─────────────────────────────────────────────
# FALLBACK-СЦЕНЫ ПО НАСТРОЕНИЮ
# ─────────────────────────────────────────────
FALLBACK_HINTS = {
    "спокойная": "sitting by window, coffee, quiet",
    "весёлая": "laughing, home, lively",
    "задумчивая": "looking out window, thinking",
    "раздражённая": "sitting alone, closed off",
    "уставшая": "lying on sofa, resting",
    "игривая": "smirking at camera, cozy room",
    "холодная": "standing, distant, minimal room",
    "тёплая": "curled up with blanket, home",
    "расстроенная": "sitting quietly, head down",
    "вдохновлённая": "at desk, curious, open",
    "нейтральная": "sitting at home, casual",
}


# ─────────────────────────────────────────────
# МАРКЕРЫ
# ─────────────────────────────────────────────
HINT_HAS_OUTFIT_MARKERS = [
    "wearing", "outfit",
    "dress", "shirt", "hoodie",
    "sweater", "pajamas", "lingerie", "bikini", "tank top", "crop top",
    "formal", "suit", "coat", "jacket", "skirt", "jeans",
    "shorts", "swimsuit", "robe", "towel", "nude", "naked", "topless",
    "bare skin", "underwear", "bra", "panties", "swimwear",
    "blanket", "plaid",
]

HINT_HAS_LOCATION_MARKERS = [
    "bedroom", "bed", "cafe", "kitchen", "bathroom", "balcony",
    "park", "street", "beach", "sofa", "window", "home",
    "office", "garden", "rooftop", "car", "forest", "couch",
]

HINT_HAS_LIGHTING_MARKERS = [
    "dimly lit", "dim light", "bright light", "soft light", "warm light",
    "cool light", "natural light", "low light", "sunlit", "moonlit",
    "candlelight", "lamp light", "backlit", "silhouette", "shadow",
    "evening light", "morning light", "afternoon light", "golden hour",
    "overcast", "muted light", "high contrast", "low contrast",
]

HINT_HAS_SHOT_MARKERS = [
    "close-up", "closeup", "close up",
    "full body", "full-body", "fullbody",
    "half body", "half-body",
    "portrait", "headshot", "bust shot",
    "waist-up", "waist up",
    "shoulder-up", "shoulder up",
    "medium shot", "wide shot", "long shot",
    "selfie", "mirror selfie",
    "three-quarter", "three quarter",
    "cowboy shot",
    "upper body", "lower body",
]


def _hint_defines_outfit(hint: str) -> bool:
    low = hint.lower()
    return any(w in low for w in HINT_HAS_OUTFIT_MARKERS)


def _hint_defines_location(hint: str) -> bool:
    low = hint.lower()
    return any(w in low for w in HINT_HAS_LOCATION_MARKERS)


def _hint_defines_lighting(hint: str) -> bool:
    low = hint.lower()
    return any(w in low for w in HINT_HAS_LIGHTING_MARKERS)


def _hint_defines_shot(hint: str) -> bool:
    low = hint.lower()
    return any(w in low for w in HINT_HAS_SHOT_MARKERS)


# ─────────────────────────────────────────────
# ОБРЕЗКА HINT ПО УРОВНЮ
# ─────────────────────────────────────────────
def _strip_overlevel_keywords(hint: str, max_level: int) -> str:
    if not hint:
        return hint

    forbidden = []
    for lvl in range(max_level + 1, 10):
        forbidden.extend(image_policy.LEVEL_KEYWORDS.get(lvl, []))

    if not forbidden:
        return hint

    parts = [p.strip() for p in hint.split(",")]
    kept = []
    for p in parts:
        if not p:
            continue
        pl = p.lower()
        if any(f in pl for f in forbidden):
            continue
        kept.append(p)

    return ", ".join(kept)


# ─────────────────────────────────────────────
# ДЕДУПЛИКАЦИЯ
# ─────────────────────────────────────────────
def _dedupe_tags(prompt: str) -> str:
    seen = set()
    out = []
    for raw in prompt.split(","):
        tag = raw.strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return ", ".join(out)


def _similar(a: str, b: str) -> float:
    wa = set(t.strip().lower() for t in a.split(",") if t.strip())
    wb = set(t.strip().lower() for t in b.split(",") if t.strip())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


# ─────────────────────────────────────────────
# СБОРКА
# ─────────────────────────────────────────────
def build_scene(
    hint: str,
    mood: str,
    user_id: int,
    last_prompts: list | None = None,
    max_attempts: int = 3,
) -> str:
    hint = (hint or "").strip()

    max_level = image_policy.effective_max_level(user_id)
    hint = _strip_overlevel_keywords(hint, max_level)

    if not hint or len(hint) < 4:
        hint = FALLBACK_HINTS.get(mood, FALLBACK_HINTS["нейтральная"])

    # ─── КЛЮЧЕВОЕ: определяем уровень СЦЕНЫ, а не пользователя ───
    scene_level = image_policy._detect_level(hint)
    outfit_level = min(scene_level, max_level)

    hint_has_outfit = _hint_defines_outfit(hint)
    hint_has_location = _hint_defines_location(hint)
    hint_has_light = _hint_defines_lighting(hint)
    hint_has_shot = _hint_defines_shot(hint)

    # outfit и location — по уровню СЦЕНЫ, а не пользователя
    outfit_tag = "" if hint_has_outfit else TRUST_OUTFIT.get(outfit_level, "")
    location_tag = "" if hint_has_location else TRUST_LOCATION.get(outfit_level, "")

    prompt = ""
    for _ in range(max_attempts):
        expr = random.choice(MOOD_EXPRESSION.get(mood, MOOD_EXPRESSION["нейтральная"]))

        parts = [hint]
        if outfit_tag:
            parts.append(outfit_tag)
        if not hint_has_shot:
            parts.append(random.choice(CAMERA_SHOTS))
        parts.append(expr)
        if not hint_has_light:
            parts.append(random.choice(MOOD_ATMOSPHERE.get(mood, MOOD_ATMOSPHERE["нейтральная"])))
        if location_tag:
            parts.append(location_tag)

        raw = ", ".join(p for p in parts if p)
        prompt = _dedupe_tags(raw)

        if not last_prompts:
            return prompt
        too_similar = any(_similar(prompt, prev) > 0.7 for prev in last_prompts[-5:])
        if not too_similar:
            return prompt

    return prompt


def get_negative_for_mood(
    mood: str,
    user_id: int | None = None,
    force_level: int | None = None,
    for_initiative: bool = False,
) -> str:
    parts = []
    m = MOOD_NEGATIVE.get(mood, "")
    if m:
        parts.append(m)

    max_level = None
    if force_level is not None:
        max_level = max(0, min(9, force_level))
    elif user_id is not None:
        max_level = image_policy.effective_max_level(user_id)

    if max_level is not None:
        t = TRUST_NEGATIVE.get(max_level, "")
        if t:
            parts.append(t)

    parts.append(CLOTHING_NEGATIVE)

    if for_initiative:
        parts.append((
            "(nude:1.7), (naked:1.7), (topless:1.7), (bottomless:1.7), "
            "(nipples:1.6), (areola:1.6), (exposed breasts:1.6), (breasts out:1.6), "
            "(see-through:1.5), (lingerie:1.5), (underwear:1.5), (nsfw:1.7), "
            "(explicit:1.7), (sex:1.7), (sexual:1.6), (undressed:1.6), "
            "(unclothed:1.6), (bare breasts:1.6), (bare chest:1.6)"
        ))

    return ", ".join(parts)