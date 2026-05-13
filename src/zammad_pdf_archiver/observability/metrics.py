from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

processed_total = Counter(
    "zammad_archiver_processed_total",
    "Number of successfully processed tickets.",
)
skipped_total = Counter(
    "zammad_archiver_skipped_total",
    "Number of skipped ticket processing attempts.",
    labelnames=("reason",),
)
failed_total = Counter(
    "zammad_archiver_failed_total",
    "Number of failed ticket processing attempts.",
)

render_seconds = Histogram(
    "zammad_archiver_render_seconds",
    "Seconds spent rendering the PDF.",
)
sign_seconds = Histogram(
    "zammad_archiver_sign_seconds",
    "Seconds spent signing the PDF.",
)
total_seconds = Histogram(
    "zammad_archiver_total_seconds",
    "Seconds spent processing a ticket end-to-end.",
)

queue_enqueued_total = Counter(
    "zammad_archiver_queue_enqueued_total",
    "Number of jobs enqueued to the durable queue.",
)
queue_processed_total = Counter(
    "zammad_archiver_queue_processed_total",
    "Number of queued jobs processed successfully.",
)
queue_retried_total = Counter(
    "zammad_archiver_queue_retried_total",
    "Number of queued jobs re-scheduled for retry.",
)
queue_failed_total = Counter(
    "zammad_archiver_queue_failed_total",
    "Number of queued jobs that failed to process in a worker.",
)
queue_dlq_total = Counter(
    "zammad_archiver_queue_dlq_total",
    "Number of queued jobs moved to dead-letter queue.",
)
ticket_lock_redis_failures_total = Counter(
    "zammad_archiver_ticket_lock_redis_failures_total",
    "Number of Redis distributed lock failures when acquiring ticket locks.",
)
tsa_failure_total = Counter(
    "zammad_archiver_tsa_failure_total",
    "Number of TSA timestamp failures while signing was configured with timestamping.",
)
tickets_in_flight = Gauge(
    "zammad_archiver_tickets_in_flight",
    "Current number of tickets held in the process-local in-flight set.",
)
queue_pending_count = Gauge(
    "zammad_archiver_queue_pending_count",
    "Current number of pending messages reported by the Redis consumer group.",
)


def render_latest(*, registry=REGISTRY) -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
