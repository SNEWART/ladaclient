# client.py — глобальные объекты: клиент Telegram, очередь, история промптов

from telethon import TelegramClient

from lada.telegram.queue import MessageQueue
from config import QUEUE_WORKER_COUNT, QUEUE_MAX_SIZE, QUEUE_DEBOUNCE_SECS


client: TelegramClient | None = None

message_queue = MessageQueue(
    worker_count=QUEUE_WORKER_COUNT,
    max_size=QUEUE_MAX_SIZE,
    debounce_secs=QUEUE_DEBOUNCE_SECS,
)

# Последние промпты сцен — для антидубля фото
recent_photo_prompts: list[str] = []


def set_client(c: TelegramClient):
    global client
    client = c


def get_client() -> TelegramClient:
    return client