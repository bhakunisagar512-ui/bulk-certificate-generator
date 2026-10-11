from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.config import get_settings

settings = get_settings()

celery_app = Celery("certgen", broker=settings.redis_url, include=["app.worker.tasks"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,  # state lives in PostgreSQL, not in a result backend
    task_acks_late=True,  # message is acknowledged after the task finishes
    task_reject_on_worker_lost=True,  # redeliver if the worker process dies mid-task
    worker_prefetch_multiplier=1,  # fair distribution, no hoarding of long tasks
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.celery_always_eager,
    task_eager_propagates=False,
    task_soft_time_limit=60,
    task_time_limit=90,
    beat_schedule={
        "recover-stuck-certificates": {"task": "certgen.recover_stuck", "schedule": 60.0},
        "cleanup-old-jobs": {"task": "certgen.cleanup_old_jobs", "schedule": crontab(minute=15, hour="*/6")},
    },
)


@worker_process_init.connect
def _reset_db_pool(**_):
    """Forked workers must not share the parent's DB connections."""
    from app.db.session import engine

    engine.dispose(close=False)
