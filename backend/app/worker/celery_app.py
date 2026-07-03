from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import get_settings, initialize_agentscope_tracing


settings = get_settings()
celery_app = Celery("dify_like", broker=settings.redis_url, backend=settings.redis_url)
app = celery_app


@worker_process_init.connect
def initialize_worker_runtime(**_kwargs) -> None:
    initialize_agentscope_tracing(settings)


@celery_app.task(name="knowledge_database.process_document")
def process_knowledge_document(document_id: str) -> None:
    from app.services.knowledge_database_service import process_knowledge_document_sync

    process_knowledge_document_sync(document_id)


@celery_app.task(name="workflow.execute_run")
def execute_workflow_run(run_id: str) -> None:
    from app.services.execution_runtime_service import execute_workflow_run_sync

    execute_workflow_run_sync(run_id)
