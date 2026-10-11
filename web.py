# web.py — веб-интерфейс управления Ладой

import asyncio
import os
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from config import MODEL, OWNER_ID
from lada.core.persona import get_persona
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.memory import (
    get_history, add_message, clear_history, list_users,
)
from lada.core import users_names
from lada.core.llm import ask
from lada.telegram.mode import get_mode, set_mode
from lada.brain.monologue import think_once
from lada.brain.reflection import reflect
from lada.brain.sleep import consolidate
from lada.brain import sins, mood
from lada.brain import image_policy
from lada.app.client import message_queue


app = FastAPI(title="Lada")

WEB_CHAT_USER_ID = 999999999
VIEW_USER_ID = OWNER_ID
WEB_UI_DIR = Path("web_ui")
IMAGES_DIR = Path("data/generated_images")

# ─────────────── Basic Auth ───────────────
WEB_AUTH_USER = os.environ.get("WEB_AUTH_USER", "")
WEB_AUTH_PASS = os.environ.get("WEB_AUTH_PASS", "")

_security = HTTPBasic(auto_error=False)


def _auth_required() -> bool:
    return bool(WEB_AUTH_USER and WEB_AUTH_PASS)


async def require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)):
    if not _auth_required():
        return True
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username, WEB_AUTH_USER)
    pass_ok = secrets.compare_digest(credentials.password, WEB_AUTH_PASS)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


if WEB_UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(WEB_UI_DIR)), name="ui")


# ─────────────── Модели запросов ───────────────

class ChatRequest(BaseModel):
    message: str

class RemoveRequest(BaseModel):
    index: int

class ModeRequest(BaseModel):
    mode: str

class TrustRequest(BaseModel):
    user_id: int
    trust: float | None = None
    intimacy: float | None = None

class MaxLevelRequest(BaseModel):
    user_id: int
    max_level: int | None = None

class StatusRequest(BaseModel):
    user_id: int
    status: str

class SleepRequest(BaseModel):
    force: bool = False


# ─────────────── UI ───────────────

@app.get("/", response_class=HTMLResponse)
async def index(_: bool = Depends(require_auth)):
    f = WEB_UI_DIR / "index.html"
    if f.exists():
        return f.read_text(encoding="utf-8")
    return "<h1>web_ui/index.html не найден</h1>"


# ─────────────── Статус ───────────────

@app.get("/api/status")
async def status(_: bool = Depends(require_auth)):
    return {
        "model": MODEL,
        "mode": get_mode(),
        "persona_size": len(get_persona()),
        "faiss": {
            "total": faiss_mem.count(),
            "emotion": faiss_mem.count_by_category("emotion", VIEW_USER_ID),
            "observation": faiss_mem.count_by_category("observation", VIEW_USER_ID),
            "reflection": faiss_mem.count_by_category("reflection", None),
            "monologue": faiss_mem.count_by_category("monologue", None),
        },
        "users": faiss_mem.list_users(),
        "mood": mood.get_mood(),
        "time": datetime.now().strftime("%H:%M:%S"),
    }


@app.get("/api/soul")
async def soul(_: bool = Depends(require_auth)):
    return {"mood": mood.get_mood(), "sins": sins.get_state()}


# ─────────────── Мысли / Рефлексии ───────────────

@app.get("/api/thoughts")
async def thoughts(_: bool = Depends(require_auth)):
    items = [m for m in faiss_mem.metadata if m.get("category") == "monologue"][-20:]
    items.reverse()
    return [
        {"text": m["text"], "timestamp": m.get("timestamp", "")[:19].replace("T", " ")}
        for m in items
    ]


@app.get("/api/reflections")
async def reflections(_: bool = Depends(require_auth)):
    items = [m for m in faiss_mem.metadata if m.get("category") == "reflection"][-20:]
    items.reverse()
    return [
        {"text": m["text"], "timestamp": m.get("timestamp", "")[:19].replace("T", " ")}
        for m in items
    ]


@app.get("/api/observations")
async def observations(_: bool = Depends(require_auth)):
    """Свежие наблюдения о владельце (последние 50)."""
    items = [
        m for m in faiss_mem.metadata
        if m.get("category") == "observation" and m.get("user_id") == VIEW_USER_ID
    ][-50:]
    items.reverse()
    return [
        {"text": m.get("text", ""),
         "timestamp": (m.get("timestamp", "") or "")[:19].replace("T", " ")}
        for m in items
    ]


# ─────────────── Режим ───────────────

@app.post("/api/mode")
async def mode_set(req: ModeRequest, _: bool = Depends(require_auth)):
    if req.mode not in ("all", "owner"):
        raise HTTPException(400, "Неверный режим")
    set_mode(req.mode)
    return {"ok": True, "mode": req.mode}


# ─────────────── Ручные действия ───────────────

@app.post("/api/think")
async def do_think(_: bool = Depends(require_auth)):
    text = await think_once()
    return {"text": text or "Пусто."}


@app.post("/api/reflect")
async def do_reflect(_: bool = Depends(require_auth)):
    await reflect()
    return {"ok": True}


@app.post("/api/sleep")
async def do_sleep(req: SleepRequest | None = None,
                    _: bool = Depends(require_auth)):
    force = bool(req.force) if req else False
    result = await consolidate(force=force)
    return {"result": result, "forced": force}


# ─────────────── Очередь ───────────────

@app.get("/api/queue")
async def queue_list(_: bool = Depends(require_auth)):
    return {"size": message_queue.size(), "items": message_queue.snapshot()}


@app.post("/api/queue/remove")
async def queue_remove(req: RemoveRequest, _: bool = Depends(require_auth)):
    ok = await message_queue.remove_at(req.index)
    if not ok:
        raise HTTPException(404, "Не найдено")
    return {"ok": True}


@app.post("/api/queue/clear")
async def queue_clear(_: bool = Depends(require_auth)):
    n = await message_queue.clear()
    return {"ok": True, "removed": n}


# ─────────────── Имена пользователей ───────────────

@app.get("/api/users_names")
async def users_names_api(_: bool = Depends(require_auth)):
    return users_names.all_names()


# ─────────────── Доверие ───────────────

@app.get("/api/trust")
async def trust_list(_: bool = Depends(require_auth)):
    from lada.brain.relationship import (
        familiarity, get_status, get_status_label, get_pending,
    )
    result = []
    for uid in list_users():
        result.append({
            "user_id": uid,
            "display_name": users_names.display_name(uid),
            "familiarity": round(familiarity(uid), 2),
            "trust": round(image_policy.get_trust(uid), 2),
            "intimacy": round(image_policy.get_intimacy(uid), 2),
            "max_level": image_policy._max_level_for_user(uid),
            "effective_max": image_policy.effective_max_level(uid),
            "max_level_override": image_policy.get_max_level_override(uid),
            "mood": mood.get_mood(),
            "is_owner": uid == OWNER_ID,
            "status": get_status(uid),
            "status_label": get_status_label(uid),
            "pending": get_pending(uid),
        })
    result.sort(key=lambda x: x["trust"], reverse=True)
    return result


@app.post("/api/trust/set")
async def trust_set(req: TrustRequest, _: bool = Depends(require_auth)):
    image_policy.set_trust(req.user_id, trust=req.trust, intimacy=req.intimacy)
    return {"ok": True}


@app.post("/api/trust/max_level")
async def trust_max_level(req: MaxLevelRequest, _: bool = Depends(require_auth)):
    image_policy.set_max_level_override(req.user_id, req.max_level)
    return {"ok": True}


@app.post("/api/trust/status")
async def trust_status(req: StatusRequest, _: bool = Depends(require_auth)):
    from lada.brain.relationship import set_status, STATUS_ORDER
    if req.status not in STATUS_ORDER:
        raise HTTPException(400, f"Статус должен быть один из: {STATUS_ORDER}")
    set_status(req.user_id, req.status, note="manual via web panel")
    return {"ok": True, "status": req.status}


@app.get("/api/trust/levels")
async def trust_levels(_: bool = Depends(require_auth)):
    return {
        "labels": image_policy.LEVEL_LABELS,
        "keywords": {str(k): v for k, v in image_policy.LEVEL_KEYWORDS.items()},
    }


# ─────────────── Список пользователей ───────────────

@app.get("/api/users/list")
async def users_list(_: bool = Depends(require_auth)):
    from lada.brain.relationship import (
        familiarity, get_status_label, get_pending,
    )
    result = []
    for uid in list_users():
        history = get_history(uid)
        obs_count = sum(
            1 for m in faiss_mem.metadata
            if m.get("category") == "observation" and m.get("user_id") == uid
        )
        emo_count = sum(
            1 for m in faiss_mem.metadata
            if m.get("category") == "emotion" and m.get("user_id") == uid
        )
        img_count = sum(
            1 for m in faiss_mem.metadata
            if m.get("category") == "image" and m.get("user_id") == uid
        )
        last_time = ""
        if history:
            last_time = (history[-1].get("timestamp", "") or "")[:19].replace("T", " ")

        result.append({
            "user_id": uid,
            "display_name": users_names.display_name(uid),
            "raw": users_names.get(uid),
            "trust": round(image_policy.get_trust(uid), 2),
            "intimacy": round(image_policy.get_intimacy(uid), 2),
            "familiarity": round(familiarity(uid), 2),
            "status_label": get_status_label(uid),
            "pending": get_pending(uid),
            "is_owner": uid == OWNER_ID,
            "observations_count": obs_count,
            "emotions_count": emo_count,
            "images_count": img_count,
            "messages_count": len(history),
            "last_time": last_time,
        })
    result.sort(key=lambda x: x["last_time"] or "", reverse=True)
    return result


# ─────────────── Профиль пользователя ───────────────

@app.get("/api/user/{user_id}/profile")
async def user_profile(user_id: int, _: bool = Depends(require_auth)):
    from lada.brain.relationship import (
        familiarity, familiarity_label, get_status, get_status_label,
        get_global_cap, get_pending,
    )

    profile = {
        "user_id": user_id,
        "display_name": users_names.display_name(user_id),
        "raw": users_names.get(user_id),
        "is_owner": user_id == OWNER_ID,
        "familiarity": round(familiarity(user_id), 3),
        "trust": round(image_policy.get_trust(user_id), 3),
        "intimacy": round(image_policy.get_intimacy(user_id), 3),
        "max_level": image_policy._max_level_for_user(user_id),
        "effective_max": image_policy.effective_max_level(user_id),
        "max_level_override": image_policy.get_max_level_override(user_id),
        "familiarity_label": familiarity_label(user_id),
        "status": get_status(user_id),
        "status_label": get_status_label(user_id),
        "global_cap": get_global_cap(user_id),
        "pending": get_pending(user_id),
    }

    def _collect(category: str):
        items = [
            {
                "text": m.get("text", ""),
                "timestamp": (m.get("timestamp", "") or "")[:19].replace("T", " "),
            }
            for m in faiss_mem.metadata
            if m.get("category") == category and m.get("user_id") == user_id
        ]
        items.reverse()
        return items

    observations = _collect("observation")
    emotions = _collect("emotion")
    images = _collect("image")

    profile["observations"] = observations[:80]
    profile["emotions"] = emotions[:80]
    profile["images"] = images[:30]
    profile["observations_count"] = len(observations)
    profile["emotions_count"] = len(emotions)
    profile["images_count"] = len(images)

    history = get_history(user_id)
    messages = [
        {
            "role": m.get("role", "?"),
            "content": m.get("content", ""),
            "timestamp": (m.get("timestamp", "") or "")[:19].replace("T", " "),
        }
        for m in history
    ]
    profile["messages"] = messages[-100:]
    profile["messages_count"] = len(messages)

    return profile


# ─────────────── Переписки ───────────────

@app.get("/api/dialogs")
async def dialogs_list(_: bool = Depends(require_auth)):
    users = list_users()
    out = []
    for uid in users:
        history = get_history(uid)
        if not history:
            continue
        last = history[-1]
        out.append({
            "user_id": uid,
            "display_name": users_names.display_name(uid),
            "raw": users_names.get(uid),
            "messages_count": len(history),
            "last_time": last.get("timestamp", "")[:19].replace("T", " "),
            "last_text": last.get("content", "")[:120],
            "last_role": last.get("role", "?"),
        })
    out.sort(key=lambda x: x["last_time"], reverse=True)
    return out


@app.get("/api/dialogs/{user_id}")
async def dialog_get(user_id: int, limit: int = 200,
                      _: bool = Depends(require_auth)):
    history = get_history(user_id)
    return [
        {
            "role": m["role"],
            "content": m["content"],
            "timestamp": m.get("timestamp", "")[:19].replace("T", " "),
        }
        for m in history[-limit:]
    ]


@app.post("/api/dialogs/{user_id}/clear")
async def dialog_clear(user_id: int, _: bool = Depends(require_auth)):
    # SQLite: удаляем из таблицы messages
    clear_history(user_id)
    return {"ok": True}


# ─────────────── Веб-чат ───────────────

@app.post("/api/chat")
async def chat(req: ChatRequest, _: bool = Depends(require_auth)):
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Пустое сообщение")

    uid = WEB_CHAT_USER_ID
    add_message(uid, "user", message, max_history=20)
    history = get_history(uid, limit=20)

    related = faiss_mem.search(message, k=5, user_id=uid)
    related = [r for r in related if r.get("category") != "emotion"][:5]
    if related:
        kt = "\n".join(f"- {k['text'][:200]}" for k in related)
        history = history + [{
            "role": "system",
            "content": f"Вот что ты уже знаешь:\n{kt}",
        }]

    content, thinking = await asyncio.to_thread(ask, history)
    reply = content or thinking or "…"

    add_message(uid, "assistant", reply, max_history=20)
    return {"reply": reply}


@app.get("/api/chat/history")
async def chat_history(_: bool = Depends(require_auth)):
    history = get_history(WEB_CHAT_USER_ID, limit=50)
    return [{"role": m["role"], "content": m["content"]} for m in history]


@app.post("/api/chat/clear")
async def chat_clear(_: bool = Depends(require_auth)):
    # SQLite: удаляем из таблицы messages
    clear_history(WEB_CHAT_USER_ID)
    return {"ok": True}
# ─────────────── Генерированные фото ───────────────

@app.get("/api/images")
async def list_images(_: bool = Depends(require_auth)):
    """Список сгенерированных фото (последние 60, свежие сначала)."""
    if not IMAGES_DIR.exists():
        return []

    files = []
    for f in IMAGES_DIR.glob("*.png"):
        try:
            st = f.stat()
            files.append({
                "name": f.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        except Exception:
            continue

    files.sort(key=lambda x: x["mtime"], reverse=True)
    files = files[:60]

    result = []
    for f in files:
        ts = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M")
        kind = "lada"
        if f["name"].startswith("cat_"):
            kind = "cat"
        result.append({
            "name": f["name"],
            "url": f"/api/images/{f['name']}",
            "size_kb": round(f["size"] / 1024, 1),
            "timestamp": ts,
            "kind": kind,
        })
    return result


@app.get("/api/images/{filename}")
async def get_image(filename: str, _: bool = Depends(require_auth)):
    """Отдаёт сам файл картинки."""
    # Защита от path traversal: оставляем только имя файла
    safe_name = Path(filename).name
    if not safe_name.endswith(".png"):
        raise HTTPException(400, "Только .png")

    path = IMAGES_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Не найдено")

    return FileResponse(str(path), media_type="image/png")


@app.delete("/api/images/{filename}")
async def delete_image(filename: str, _: bool = Depends(require_auth)):
    """Удаляет файл картинки."""
    safe_name = Path(filename).name
    if not safe_name.endswith(".png"):
        raise HTTPException(400, "Только .png")

    path = IMAGES_DIR / safe_name
    if not path.exists():
        raise HTTPException(404, "Не найдено")

    try:
        path.unlink()
    except Exception as e:
        raise HTTPException(500, f"Не удалось удалить: {e}")

    return {"ok": True, "name": safe_name}