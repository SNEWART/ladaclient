# web_ui/memory_api.py — API для вкладки «Память» в веб-панели.
#
# Просмотр, поиск, редактирование, удаление записей LanceDB.
# Идентификация записи — (text, user_id, timestamp).
# Редактирование = удалить старую + добавить новую с тем же meta
# (эмбеддинг пересчитывается автоматически).

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from lada.core.lancedb_memory import lance_mem
from lada.core import users_names
from web_ui.auth import require_auth

router = APIRouter(prefix="/api/memory", tags=["memory"])


class DeleteRequest(BaseModel):
    text: str
    user_id: int | None = None
    timestamp: str = ""


class EditRequest(BaseModel):
    text: str
    user_id: int | None = None
    timestamp: str = ""
    new_text: str


def _safe_uid(uid):
    return -1 if uid is None else int(uid)


# ─────────────── список ───────────────

@router.get("/list")
async def list_memory(
    category: str = "",
    user_id: str = "",
    q: str = "",
    valence_min: float | None = None,
    valence_max: float | None = None,
    has_meta: str = "",
    limit: int = 100,
    offset: int = 0,
    _: bool = Depends(require_auth),
):
    all_items = list(lance_mem.metadata)

    # Фильтрация
    result = []
    uid_filter = None
    if user_id.strip() and user_id.strip() != "all":
        try:
            uid_filter = int(user_id)
        except ValueError:
            uid_filter = None

    cat_filter = category.strip().lower() if category.strip() else None
    q_low = q.strip().lower() if q.strip() else None

    meta_mode = (has_meta or "").strip().lower()

    for m in all_items:
        if cat_filter and (m.get("category") or "").lower() != cat_filter:
            continue

        item_uid = m.get("user_id")
        if uid_filter is not None:
            if uid_filter == -1:
                if item_uid is not None:
                    continue
            else:
                if item_uid != uid_filter:
                    continue

        if q_low:
            if q_low not in (m.get("text") or "").lower():
                continue

        # valence-фильтр
        if valence_min is not None or valence_max is not None:
            v = m.get("valence")
            if v is None:
                continue
            try:
                vf = float(v)
            except (TypeError, ValueError):
                continue
            if valence_min is not None and vf < valence_min:
                continue
            if valence_max is not None and vf > valence_max:
                continue

        if meta_mode == "yes" and m.get("valence") is None:
            continue
        if meta_mode == "no" and m.get("valence") is not None:
            continue

        result.append(m)

    # Сортировка — свежие сначала
    result.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    total = len(result)
    page = result[offset:offset + limit]

    # Обогащение
    out = []
    for m in page:
        uid = m.get("user_id")
        out.append({
            "text": m.get("text", ""),
            "category": m.get("category", "?"),
            "source": m.get("source", ""),
            "user_id": uid,
            "user_name": (users_names.display_name(uid)
                          if uid is not None else "общее"),
            "timestamp": (m.get("timestamp") or "")[:19].replace("T", " "),
            "valence": m.get("valence"),
            "mood_at_time": m.get("mood_at_time"),
            "trust_at_time": m.get("trust_at_time"),
            "intimacy_at_time": m.get("intimacy_at_time"),
            "top_sins": m.get("top_sins") or [],
        })

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": out,
    }


# ─────────────── статистика ───────────────

@router.get("/stats")
async def stats(_: bool = Depends(require_auth)):
    items = list(lance_mem.metadata)

    by_cat = {}
    by_user = {}
    with_meta = 0
    valence_pos = 0
    valence_neg = 0
    valence_neutral = 0

    for m in items:
        cat = m.get("category", "?")
        by_cat[cat] = by_cat.get(cat, 0) + 1

        uid = m.get("user_id")
        key = "общее" if uid is None else str(uid)
        by_user[key] = by_user.get(key, 0) + 1

        if m.get("valence") is not None:
            with_meta += 1
            try:
                v = float(m.get("valence"))
                if v > 0.1:
                    valence_pos += 1
                elif v < -0.1:
                    valence_neg += 1
                else:
                    valence_neutral += 1
            except (TypeError, ValueError):
                pass

    # Имена пользователей
    user_names = {}
    for k in by_user:
        if k == "общее":
            continue
        try:
            user_names[k] = users_names.display_name(int(k))
        except Exception:
            user_names[k] = f"ID {k}"

    return {
        "total": len(items),
        "by_category": by_cat,
        "by_user": by_user,
        "user_names": user_names,
        "with_meta": with_meta,
        "without_meta": len(items) - with_meta,
        "valence_pos": valence_pos,
        "valence_neg": valence_neg,
        "valence_neutral": valence_neutral,
    }


# ─────────────── удаление ───────────────

def _find_row(text: str, user_id, timestamp: str):
    target_uid = -1 if user_id is None else int(user_id)
    text = (text or "").strip()
    for m in lance_mem.metadata:
        if (m.get("text") or "").strip() != text:
            continue
        m_uid = m.get("user_id")
        if (m_uid or -1) != target_uid:
            continue
        if timestamp:
            m_ts = (m.get("timestamp") or "")[:19]
            if m_ts != timestamp[:19]:
                continue
        return m
    return None


@router.post("/delete")
async def delete_memory(req: DeleteRequest,
                        _: bool = Depends(require_auth)):
    row = _find_row(req.text, req.user_id, req.timestamp)
    if not row:
        raise HTTPException(404, "Запись не найдена")

    target_uid = -1 if req.user_id is None else int(req.user_id)
    safe_text = req.text.replace("'", "''")

    if req.timestamp:
        ts = req.timestamp[:19]
        where = (f"text = '{safe_text}' AND user_id = {target_uid} "
                 f"AND timestamp LIKE '{ts}%'")
    else:
        where = f"text = '{safe_text}' AND user_id = {target_uid}"

    try:
        lance_mem.table.delete(where)
    except Exception as e:
        raise HTTPException(500, f"Не удалось удалить: {e}")

    lance_mem._invalidate_meta_cache()
    return {"ok": True}


# ─────────────── редактирование ───────────────

@router.post("/edit")
async def edit_memory(req: EditRequest,
                      _: bool = Depends(require_auth)):
    row = _find_row(req.text, req.user_id, req.timestamp)
    if not row:
        raise HTTPException(404, "Запись не найдена")

    new_text = (req.new_text or "").strip()
    if len(new_text) < 3:
        raise HTTPException(400, "Слишком короткий текст")

    # Сохраняем meta и прочие поля
    meta = {}
    if row.get("valence") is not None:
        meta = {
            "valence": row.get("valence"),
            "mood_at_time": row.get("mood_at_time"),
            "trust_at_time": row.get("trust_at_time"),
            "intimacy_at_time": row.get("intimacy_at_time"),
            "top_sins": row.get("top_sins") or [],
        }

    target_uid = -1 if req.user_id is None else int(req.user_id)
    safe_text = req.text.replace("'", "''")

    if req.timestamp:
        ts = req.timestamp[:19]
        where = (f"text = '{safe_text}' AND user_id = {target_uid} "
                 f"AND timestamp LIKE '{ts}%'")
    else:
        where = f"text = '{safe_text}' AND user_id = {target_uid}"

    try:
        lance_mem.table.delete(where)
    except Exception as e:
        raise HTTPException(500, f"Не удалось заменить: {e}")

    # Добавляем новую с тем же meta
    uid_for_add = None if target_uid == -1 else target_uid
    ok = lance_mem.add(
        new_text,
        category=row.get("category", "general"),
        source=row.get("source", "manual_edit"),
        user_id=uid_for_add,
        meta=meta or None,
    )
    if not ok:
        raise HTTPException(500, "Не удалось записать новую версию")

    lance_mem._invalidate_meta_cache()
    return {"ok": True}


# ─────────────── категории и юзеры (для фильтров) ───────────────

@router.get("/filters")
async def filters(_: bool = Depends(require_auth)):
    items = list(lance_mem.metadata)

    cats = sorted({(m.get("category") or "?").lower() for m in items})

    uids = sorted({
        m.get("user_id") for m in items
        if m.get("user_id") is not None
    })

    users = []
    for u in uids:
        try:
            users.append({
                "user_id": u,
                "name": users_names.display_name(u),
            })
        except Exception:
            users.append({"user_id": u, "name": f"ID {u}"})

    return {
        "categories": cats,
        "users": users,
    }