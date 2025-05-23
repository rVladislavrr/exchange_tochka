from celery import Celery

from src.config import settings


celery_app = Celery(
    "worker",
    broker=f"{settings.REDIS_BASE_URL}/0",  # или REDIS_URL из .env
    backend=f"{settings.REDIS_BASE_URL}/1",
    include=["src.tasks.celery_tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='UTC',
    enable_utc=True,
)
