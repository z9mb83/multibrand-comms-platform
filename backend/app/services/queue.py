"""Background execution. 'inline' runs work in-process (dev/tests); 'cloud_tasks' enqueues HTTP tasks
that call /internal/dispatch/{id}. Task names are derived from the message id, so duplicate enqueues
are de-duplicated by Cloud Tasks, and process_outbound itself is idempotent anyway."""
import logging

from app.core.config import get_settings
from app.core.db import session_factory

log = logging.getLogger(__name__)


def enqueue_dispatch(message_ids: list[int]) -> None:
    s = get_settings()
    if not message_ids:
        return
    if s.task_backend == "inline":
        from app.services.dispatch import process_outbound
        for mid in message_ids:
            try:
                process_outbound(session_factory(), mid)
            except Exception:  # pragma: no cover - logged, retried by sweep
                log.exception("inline dispatch failed for %s", mid)
        return
    from google.cloud import tasks_v2  # type: ignore

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(s.gcp_project, s.gcp_region, s.cloud_tasks_queue)
    for mid in message_ids:
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{s.internal_base_url}/internal/dispatch/{mid}",
                "oidc_token": {"audience": s.internal_oidc_audience or s.internal_base_url},
            }
        }
        try:
            client.create_task(parent=parent, task=task)
        except Exception:  # pragma: no cover
            log.exception("enqueue failed for %s (dispatch sweep will pick it up)", mid)
