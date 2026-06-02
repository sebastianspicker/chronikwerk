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
processed_partial_total = Counter(
    "zammad_archiver_processed_partial_total",
    "Number of processed tickets whose archive was written with capped or skipped content.",
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
history_record_failed_total = Counter(
    "zammad_archiver_history_record_failed_total",
    "Number of ticket processing results whose history event could not be recorded.",
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
queue_partial_total = Counter(
    "zammad_archiver_queue_partial_total",
    "Number of queued jobs completed with partial success.",
)
queue_skipped_total = Counter(
    "zammad_archiver_queue_skipped_total",
    "Number of queued jobs skipped by process-ticket status.",
    labelnames=("reason",),
)
queue_unknown_status_total = Counter(
    "zammad_archiver_queue_unknown_status_total",
    "Number of queued jobs with an unrecognized process-ticket status.",
)
queue_stale_pending_claim_failed_total = Counter(
    "zammad_archiver_queue_stale_pending_claim_failed_total",
    "Number of failed Redis scans while trying to recover stale pending queue messages.",
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
ticket_lock_redis_release_failures_total = Counter(
    "zammad_archiver_ticket_lock_redis_release_failures_total",
    "Number of Redis distributed lock release failures that may leave stale locks.",
)
redis_store_close_failures_total = Counter(
    "zammad_archiver_redis_store_close_failures_total",
    "Number of Redis-backed ticket/idempotency store close failures during shutdown.",
)
redis_pool_close_failures_total = Counter(
    "zammad_archiver_redis_pool_close_failures_total",
    "Number of cached Redis client close failures during shutdown.",
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
