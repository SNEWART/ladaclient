# bench_models.py — бенчмарк моделей Ollama.
#
# Замеряет для каждой модели:
#   - время загрузки (первый запрос)
#   - prompt eval (обработка входа) в tok/s
#   - скорость генерации (eval) в tok/s
#   - время до первого токена (TTFT)
#   - потребление VRAM (size_vram из /api/ps)
#
# Запуск:
#   python bench_models.py
#   python bench_models.py --prompt 500     # длина промпта в токенах (~символов/4)
#   python bench_models.py --num-predict 200
#
# Модели берутся из списка MODELS_TO_TEST ниже.

import argparse
import time
import sys

import ollama


# ─── Список моделей для теста ───
MODELS_TO_TEST = [
    "igorls/gemma-4-E4B-it-heretic-GGUF:Q5_K_M",
    "richardyoung/gemma-2-9b-it-abliterated:Q4_K_M",
    # Добавь сюда свои, если хочешь сравнить:
    # "huihui_ai/qwen2.5-abliterate:1.5b",
    # "anthracite-org/magnum-v4-9b:Q4_K_M",
]

# Сколько раз повторять замер для усреднения
RUNS = 2

# Длина промпта по умолчанию (в символах, ~4 символа на токен)
DEFAULT_PROMPT_CHARS = 4000

# Сколько токенов генерировать
DEFAULT_NUM_PREDICT = 200


def _print_sep(title: str = ""):
    print()
    print("=" * 70)
    if title:
        print(title)
        print("=" * 70)


def _fmt_sec(x: float) -> str:
    if x < 1:
        return f"{x*1000:.0f} мс"
    return f"{x:.2f} с"


def get_vram(model: str) -> float:
    """Возвращает размер модели в VRAM (в МБ) или 0, если не загружена."""
    try:
        ps = ollama.ps()
        for m in ps.get("models", []):
            name = m.get("name") or m.get("model") or ""
            if name == model or name.startswith(model.split(":")[0]):
                size = m.get("size_vram") or 0
                return size / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def unload(model: str):
    """Выгружает модель из VRAM."""
    try:
        ollama.generate(model=model, prompt="", keep_alive=0)
    except Exception:
        pass
    time.sleep(1)


def make_prompt(chars: int) -> str:
    """Собирает большой промпт заданной длины."""
    base = (
        "Ты — Лада, девушка 22 лет. Ты общаешься с пользователем в мессенджере. "
        "Отвечай живо, коротко, без канцелярита. "
    )
    filler = (
        "Вот что ты помнишь о собеседнике: он любит горы, работает в IT, "
        "общается прямо, ценит конкретику. Вы говорили о работе, о кофе, "
        "о кошке Мусе. Он недавно был уставшим, но настроение у него хорошее. "
    )
    out = base
    while len(out) < chars:
        out += filler
    return out[:chars] + "\n\nПользователь: как дела?\nЛада:"


def bench_model(model: str, prompt: str, num_predict: int) -> dict | None:
    """
    Запускает бенчмарк для одной модели.
    Возвращает dict с метриками или None при ошибке.
    """
    print(f"\n📊 Модель: {model}")

    # Проверка наличия
    try:
        ollama.show(model)
    except Exception as e:
        print(f"   ❌ Модель не найдена: {e}")
        return None

    # Прогрев: первая загрузка не считается
    print("   ⏳ Прогрев (загрузка в VRAM)...")
    try:
        t0 = time.time()
        ollama.generate(
            model=model,
            prompt="привет",
            options={"num_predict": 5},
            keep_alive="5m",
        )
        warm_time = time.time() - t0
    except Exception as e:
        print(f"   ❌ Прогрев упал: {e}")
        return None

    vram = get_vram(model)
    print(f"   ✅ Загружена за {_fmt_sec(warm_time)} "
          f"(VRAM: {vram:.0f} МБ)")

    # Замеры
    results = []
    for run in range(1, RUNS + 1):
        print(f"   🔄 Прогон {run}/{RUNS}...")
        try:
            t0 = time.time()
            ttft = None
            final_chunk = None
            gen_tokens = 0

            stream = ollama.generate(
                model=model,
                prompt=prompt,
                stream=True,
                options={"num_predict": num_predict},
                keep_alive="5m",
            )

            for chunk in stream:
                if ttft is None:
                    ttft = time.time() - t0
                final_chunk = chunk
                if chunk.get("response"):
                    gen_tokens += 1

            total_time = time.time() - t0

            if final_chunk is None:
                print("   ⚠️ Пустой ответ")
                continue

            # Ollama отдаёт точные метрики в последнем чанке
            eval_count = final_chunk.get("eval_count", 0)
            eval_duration = final_chunk.get("eval_duration", 0)  # нс
            prompt_eval_count = final_chunk.get("prompt_eval_count", 0)
            prompt_eval_duration = final_chunk.get("prompt_eval_duration", 0)

            gen_tps = 0.0
            if eval_duration > 0:
                gen_tps = eval_count / (eval_duration / 1e9)

            prompt_tps = 0.0
            if prompt_eval_duration > 0:
                prompt_tps = prompt_eval_count / (prompt_eval_duration / 1e9)

            results.append({
                "ttft": ttft,
                "total_time": total_time,
                "gen_tokens": eval_count,
                "gen_tps": gen_tps,
                "prompt_tokens": prompt_eval_count,
                "prompt_tps": prompt_tps,
            })

        except Exception as e:
            print(f"   ❌ Прогон упал: {e}")
            continue

    if not results:
        return None

    # Усредняем
    avg = lambda k: sum(r[k] for r in results) / len(results)
    best = lambda k: max(r[k] for r in results)

    print()
    print(f"   ─── Результаты ({model.split(':')[0]}) ───")
    print(f"   Prompt eval:    {avg('prompt_tps'):>8.1f} tok/s  "
          f"({int(avg('prompt_tokens'))} токенов входа)")
    print(f"   Генерация:      {avg('gen_tps'):>8.1f} tok/s  "
          f"(лучший прогон: {best('gen_tps'):.1f} tok/s)")
    print(f"   TTFT:           {_fmt_sec(avg('ttft'))}")
    print(f"   Всего времени:  {_fmt_sec(avg('total_time'))}  "
          f"({int(avg('gen_tokens'))} токенов)")
    print(f"   VRAM:           {vram:.0f} МБ")

    return {
        "model": model,
        "vram_mb": vram,
        "prompt_tps": avg("prompt_tps"),
        "gen_tps": avg("gen_tps"),
        "ttft": avg("ttft"),
        "total_time": avg("total_time"),
        "gen_tokens": avg("gen_tokens"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt", type=int, default=DEFAULT_PROMPT_CHARS,
        help=f"Длина промпта в символах (по умолч. {DEFAULT_PROMPT_CHARS})",
    )
    parser.add_argument(
        "--num-predict", type=int, default=DEFAULT_NUM_PREDICT,
        help=f"Сколько токенов генерировать (по умолч. {DEFAULT_NUM_PREDICT})",
    )
    args = parser.parse_args()

    # Проверяем ollama вообще
    try:
        v = ollama.list()
    except Exception as e:
        print(f"❌ Ollama недоступна: {e}")
        sys.exit(1)

    _print_sep("БЕНЧМАРК МОДЕЛЕЙ")
    print(f"Моделей в списке: {len(MODELS_TO_TEST)}")
    print(f"Промпт: ~{args.prompt} символов (~{args.prompt // 4} токенов)")
    print(f"Генерация: {args.num_predict} токенов")
    print(f"Прогонов на модель: {RUNS}")

    prompt = make_prompt(args.prompt)

    all_results = []
    for model in MODELS_TO_TEST:
        res = bench_model(model, prompt, args.num_predict)
        if res:
            all_results.append(res)
        unload(model)

    # ─── Итоговая таблица ───
    if not all_results:
        print("\n⚠️ Ни одной модели не удалось протестировать.")
        return

    _print_sep("СРАВНЕНИЕ")
    print(f"{'Модель':<50} {'Gen tok/s':>10} {'Prompt tok/s':>13} "
          f"{'TTFT':>8} {'VRAM':>8}")
    print("-" * 90)
    for r in sorted(all_results, key=lambda x: x["gen_tps"], reverse=True):
        name = r["model"].split(":")[0]
        if len(name) > 48:
            name = name[:45] + "..."
        print(f"{name:<50} {r['gen_tps']:>10.1f} {r['prompt_tps']:>13.1f} "
              f"{r['ttft']*1000:>6.0f}мс {r['vram_mb']:>6.0f}МБ")

    print()
    print("Расшифровка:")
    print("  Gen tok/s     — скорость генерации ответа. Больше = быстрее отвечает.")
    print("  Prompt tok/s  — скорость обработки промпта. Больше = меньше думает над входом.")
    print("  TTFT          — время до первого токена. Меньше = быстрее начинает печатать.")
    print("  VRAM          — сколько занимает в видеопамяти.")


if __name__ == "__main__":
    main()