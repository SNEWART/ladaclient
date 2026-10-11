# web_ui/persona_api.py — API редактора персоны
from fastapi import APIRouter, HTTPException, Body
from pathlib import Path
from datetime import datetime
import shutil

router = APIRouter(prefix="/api/persona", tags=["persona"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PERSONA_DIR = PROJECT_ROOT / "persona"
HISTORY_DIR = PERSONA_DIR / "_history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

MAX_HISTORY = 20
MAX_SIZE = 500_000  # 500 KB


def _safe_name(name: str) -> str:
    name = Path(name).name
    if not name.endswith(".md"):
        raise HTTPException(400, "Только .md файлы")
    if name.startswith("."):
        raise HTTPException(400, "Недопустимое имя")
    return name


@router.get("")
async def list_persona():
    items = []
    for f in sorted(PERSONA_DIR.glob("*.md")):
        st = f.stat()
        items.append({
            "name": f.name,
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return items


@router.get("/{name}")
async def get_persona(name: str):
    name = _safe_name(name)
    path = PERSONA_DIR / name
    if not path.exists():
        raise HTTPException(404, "Файл не найден")
    st = path.stat()
    return {
        "name": name,
        "content": path.read_text(encoding="utf-8"),
        "size": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.put("/{name}")
async def save_persona(name: str, payload: dict = Body(...)):
    name = _safe_name(name)
    content = payload.get("content", "")
    if len(content.encode("utf-8")) > MAX_SIZE:
        raise HTTPException(413, "Файл слишком большой")

    path = PERSONA_DIR / name
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, HISTORY_DIR / f"{name}.{ts}.bak")
        versions = sorted(HISTORY_DIR.glob(f"{name}.*.bak"))
        for old in versions[:-MAX_HISTORY]:
            try:
                old.unlink()
            except Exception:
                pass

    path.write_text(content, encoding="utf-8")
    return {"ok": True, "modified": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@router.get("/{name}/history")
async def get_history(name: str):
    name = _safe_name(name)
    versions = sorted(HISTORY_DIR.glob(f"{name}.*.bak"), reverse=True)
    return [
        {
            "id": v.name,
            "timestamp": v.name.split(".")[-2],
            "size": v.stat().st_size,
        }
        for v in versions
    ]


@router.get("/{name}/history/{version_id}")
async def get_version(name: str, version_id: str):
    name = _safe_name(name)
    version_id = Path(version_id).name
    if not version_id.startswith(name + "."):
        raise HTTPException(400, "Неверная версия")
    path = HISTORY_DIR / version_id
    if not path.exists():
        raise HTTPException(404, "Версия не найдена")
    return {"content": path.read_text(encoding="utf-8")}


@router.post("/{name}/restore/{version_id}")
async def restore_version(name: str, version_id: str):
    name = _safe_name(name)
    version_id = Path(version_id).name
    src = HISTORY_DIR / version_id
    if not src.exists():
        raise HTTPException(404, "Версия не найдена")
    dst = PERSONA_DIR / name
    if dst.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(dst, HISTORY_DIR / f"{name}.{ts}.bak")
    shutil.copy2(src, dst)
    return {"ok": True}