# image_gen.py — генерация изображений (SDXL).
#
# Два режима:
#   subject="lada" — фото Лады (appearance Лады + сцена)
#   subject="pet"  — фото только кошки (appearance кошки, без людей)
#
# У каждого режима свой фиксированный seed: лицо Лады и морда кошки
# стабильны между генерациями. Sampler — Euler a.

import base64
import time
import requests
from pathlib import Path
from datetime import datetime
from config import MODEL

OUTPUT_DIR = Path("data/generated_images")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SD_BUSY_MARKER = Path("data") / ".sd_busy"

OLLAMA_URL = "http://localhost:11434"
SD_URL = "http://localhost:7860"

APPEARANCE_FILE = Path("persona") / "image_appearance.md"
PET_APPEARANCE_FILE = Path("persona") / "pet_appearance.md"

# ─── SEED ───
# Фиксированные сиды для стабильной внешности
LADA_SEED = 608568213
PET_SEED = 774216091

# ─── ПАРАМЕТРЫ SDXL ───
DEFAULT_WIDTH = 544
DEFAULT_HEIGHT = 768
DEFAULT_STEPS = 22
DEFAULT_CFG = 8.0
DEFAULT_SAMPLER = "Euler a"

QUALITY_PREFIX = "masterpiece, best quality, highly detailed, absurdres"

BASE_NEGATIVE = (
    "(worst quality, low quality:1.4), (bad anatomy:1.2), (bad hands:1.2), "
    "extra fingers, fewer digits, missing fingers, cropped, jpeg artifacts, "
    "signature, watermark, username, simple background, "
    "jewelry, necklace, earrings, rings, bracelet, "
    "armor, weapon, gun, sword, "
    "(multiple girls, 2girls, crowd, man, male:1.3)"
)

PET_NEGATIVE = (
    "(worst quality, low quality:1.4), (bad anatomy:1.2), "
    "extra limbs, missing limbs, extra tails, deformed paws, "
    "cropped, jpeg artifacts, signature, watermark, "
    "(person, girl, woman, man, human, hands, arms, face:1.5), "
    "multiple cats, crowd"
)

DEFAULT_PET_APPEARANCE = (
    "1cat, solo, adult female cat, domestic short hair, gray tabby fur, "
    "green eyes, pink nose, fluffy tail, natural pose"
)


def _read_file(path: Path) -> str:
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8").strip()
            return " ".join(line.strip() for line in text.splitlines() if line.strip())
        except Exception as e:
            print(f"   ⚠️ Не удалось прочитать {path}: {e}")
    return ""


def _load_lada_appearance() -> str:
    a = _read_file(APPEARANCE_FILE)
    return a or "1girl, solo, 22 years old"


def _load_pet_appearance() -> str:
    a = _read_file(PET_APPEARANCE_FILE)
    return a or DEFAULT_PET_APPEARANCE


def unload_ollama(model: str = None):
    model = model or MODEL
    try:
        requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=10,
        )
        time.sleep(4)
        print(f"   🧹 Ollama: выгружена")
    except Exception as e:
        print(f"   ⚠️ Не удалось выгрузить Ollama: {e}")


def unload_sd():
    try:
        requests.post(f"{SD_URL}/sdapi/v1/unload-checkpoint", timeout=10)
        time.sleep(2)
        print(f"   🧹 SD-модель выгружена")
    except Exception as e:
        print(f"   ⚠️ Не удалось выгрузить SD: {e}")


def build_prompt(scene: str, subject: str = "lada") -> str:
    if subject == "pet":
        appearance = _load_pet_appearance()
    else:
        appearance = _load_lada_appearance()
    parts = [QUALITY_PREFIX, appearance, scene.strip()]
    return ", ".join(p for p in parts if p)


def generate(
    scene_prompt: str,
    negative_prompt: str = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    steps: int = DEFAULT_STEPS,
    cfg_scale: float = DEFAULT_CFG,
    seed: int = None,
    sampler: str = DEFAULT_SAMPLER,
    subject: str = "lada",
) -> str | None:
    """
    subject: "lada" | "pet"
    Если seed не передан — берётся LADA_SEED или PET_SEED по subject.
    """
    if seed is None:
        seed = PET_SEED if subject == "pet" else LADA_SEED

    if negative_prompt is None:
        negative_prompt = PET_NEGATIVE if subject == "pet" else BASE_NEGATIVE

    try:
        SD_BUSY_MARKER.touch()
    except Exception:
        pass

    try:
        full_prompt = build_prompt(scene_prompt, subject=subject)
        print(f"   📝 Промпт ({subject}): {full_prompt[:200]}...")
        print(f"   🎲 Seed ({subject}): {seed}")
        print(f"   🎛 Sampler: {sampler}")

        unload_ollama()

        payload = {
            "prompt": full_prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "seed": seed,
            "sampler_name": sampler,
            "batch_size": 1,
            "n_iter": 1,
        }

        try:
            r = requests.post(f"{SD_URL}/sdapi/v1/txt2img", json=payload, timeout=600)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"   ⚠️ Ошибка SD: {e}")
            return None

        images = data.get("images", [])
        if not images:
            return None

        img_bytes = base64.b64decode(images[0])
        prefix = "cat" if subject == "pet" else "lada"
        filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = OUTPUT_DIR / filename
        path.write_bytes(img_bytes)

        print(f"   🖼 Сохранено: {path}")

        unload_sd()

        return str(path)

    finally:
        try:
            SD_BUSY_MARKER.unlink(missing_ok=True)
        except Exception:
            pass