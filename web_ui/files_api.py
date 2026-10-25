# web_ui/files_api.py — файловый менеджер: список, чтение, запись,
# загрузка, скачивание, создание, переименование, удаление.
#
# Корень — папка проекта. Скрыты: venv, .git, __pycache__,
# data/lancedb, data/backups.

import io
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from web_ui.auth import require_auth

router = APIRouter(prefix="/api/files", tags=["files"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BLOCKED_DIRS = {"venv", ".venv", "__pycache__", ".git"}
BLOCKED_REL = {"data/lancedb", "data/backups"}

MAX_UPLOAD = 200 * 1024 * 1024       # 200 МБ
MAX_TEXT_PREVIEW = 2 * 1024 * 1024   # 2 МБ для редактора

TEXT_EXTS = {
    ".py", ".js", ".css", ".html", ".htm", ".json", ".md", ".txt",
    ".ini", ".cfg", ".env", ".yml", ".yaml", ".toml", ".sql",
    ".bat", ".sh", ".log", ".vue", ".ts", ".tsx", ".jsx",
    ".gitignore", ".dockerfile", ".xml", ".csv",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}


def _safe_path(rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/").replace("\\", "/")
    p = (PROJECT_ROOT / rel).resolve()
    try:
        p.relative_to(PROJECT_ROOT)
    except ValueError:
        raise HTTPException(403, "Путь вне проекта")

    for parent in [p, *p.parents]:
        if parent.name in BLOCKED_DIRS:
            raise HTTPException(403, f"Запрещено: {parent.name}")

    try:
        rel_str = str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        raise HTTPException(403, "Путь вне проекта")

    for b in BLOCKED_REL:
        if rel_str == b or rel_str.startswith(b + "/"):
            raise HTTPException(403, f"Запрещено: {b}")

    return p


def _rel(p: Path) -> str:
    return str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")


def _kind(p: Path) -> str:
    if p.is_dir():
        return "dir"
    ext = p.suffix.lower()
    if ext in TEXT_EXTS or p.name.startswith("."):
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    return "file"


def _is_blocked(p: Path) -> bool:
    if p.name in BLOCKED_DIRS:
        return True
    try:
        rel = _rel(p)
    except ValueError:
        return True
    for b in BLOCKED_REL:
        if rel == b or rel.startswith(b + "/"):
            return True
    return False


# ─────────────── роуты ───────────────

@router.get("")
async def list_dir(path: str = "", _: bool = Depends(require_auth)):
    d = _safe_path(path)
    if not d.exists():
        raise HTTPException(404, "Не найдено")
    if not d.is_dir():
        raise HTTPException(400, "Не директория")

    items = []
    for f in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if _is_blocked(f):
            continue
        try:
            st = f.stat()
            items.append({
                "name": f.name,
                "path": _rel(f),
                "kind": _kind(f),
                "size": st.st_size if f.is_file() else 0,
                "modified": datetime.fromtimestamp(st.st_mtime)
                    .strftime("%Y-%m-%d %H:%M"),
            })
        except Exception:
            continue

    cur = _rel(d) if d != PROJECT_ROOT else ""
    parent = _rel(d.parent) if d != PROJECT_ROOT else None
    return {"path": cur, "parent": parent, "items": items}


@router.get("/raw")
async def download(path: str, _: bool = Depends(require_auth)):
    p = _safe_path(path)
    if not p.is_file():
        raise HTTPException(404, "Не файл")
    return FileResponse(str(p), filename=p.name)


@router.get("/read")
async def read_text(path: str, _: bool = Depends(require_auth)):
    p = _safe_path(path)
    if not p.is_file():
        raise HTTPException(404, "Не файл")
    if p.stat().st_size > MAX_TEXT_PREVIEW:
        raise HTTPException(413, "Файл слишком большой для редактора")
    try:
        return {"content": p.read_text(encoding="utf-8", errors="replace"),
                "size": p.stat().st_size}
    except Exception as e:
        raise HTTPException(500, f"Не читается: {e}")


@router.put("/write")
async def write_text(path: str, payload: dict = Body(...),
                     _: bool = Depends(require_auth)):
    p = _safe_path(path)
    content = payload.get("content", "")
    if len(content.encode("utf-8")) > MAX_TEXT_PREVIEW:
        raise HTTPException(413, "Слишком большой")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": _rel(p)}


@router.post("/upload")
async def upload(path: str = "", file: UploadFile = File(...),
                 _: bool = Depends(require_auth)):
    folder = _safe_path(path)
    if not folder.is_dir():
        raise HTTPException(400, "Не директория")

    name = Path(file.filename or "file").name
    target = (folder / name).resolve()
    try:
        target.relative_to(folder)
    except ValueError:
        raise HTTPException(400, "Неверное имя файла")

    total = 0
    with open(target, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD:
                f.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, "Файл слишком большой")
            f.write(chunk)

    return {"ok": True, "path": _rel(target), "size": total}


@router.post("/mkdir")
async def mkdir(path: str = "", name: str = Body(..., embed=True),
                _: bool = Depends(require_auth)):
    folder = _safe_path(path)
    if not folder.is_dir():
        raise HTTPException(400, "Не директория")
    name = Path(name).name
    if not name:
        raise HTTPException(400, "Пустое имя")
    target = folder / name
    if target.exists():
        raise HTTPException(409, "Уже существует")
    target.mkdir(parents=True)
    return {"ok": True, "path": _rel(target)}


@router.post("/rename")
async def rename(path: str, new_name: str = Body(..., embed=True),
                 _: bool = Depends(require_auth)):
    p = _safe_path(path)
    if not p.exists():
        raise HTTPException(404, "Не найдено")
    new_name = Path(new_name).name
    if not new_name:
        raise HTTPException(400, "Пустое имя")
    target = p.parent / new_name
    if target.exists():
        raise HTTPException(409, "Уже существует")
    p.rename(target)
    return {"ok": True, "path": _rel(target)}


@router.delete("")
async def delete(path: str, _: bool = Depends(require_auth)):
    p = _safe_path(path)
    if not p.exists():
        raise HTTPException(404, "Не найдено")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"ok": True}