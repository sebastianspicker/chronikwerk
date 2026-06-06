from __future__ import annotations

from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.idempotency import DeliveryIdStore, InMemoryTTLSet
from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore


def redis_store(
    stores: dict[tuple[str, int, str | None], RedisDeliveryIdStore],
    redis_url: str,
    ttl_seconds: int,
    prefix: str | None = None,
) -> RedisDeliveryIdStore:
    cache_key = (redis_url, ttl_seconds, prefix)
    result = stores.get(cache_key)
    if result is None:
        if prefix is None:
            result = RedisDeliveryIdStore(redis_url, ttl_seconds)
        else:
            result = RedisDeliveryIdStore(redis_url, ttl_seconds, prefix=prefix)
        stores[cache_key] = result
    return result


def delivery_id_store(
    settings: Settings,
    *,
    memory_sets: dict[int, InMemoryTTLSet],
    redis_stores: dict[tuple[str, int, str | None], RedisDeliveryIdStore],
) -> DeliveryIdStore | None:
    ttl = int(settings.workflow.delivery_id_ttl_seconds)
    if ttl <= 0 or is_shutting_down():
        return None
    backend = (settings.workflow.idempotency_backend or "memory").strip().lower()
    if backend == "redis" and settings.workflow.redis_url:
        return redis_store(redis_stores, settings.workflow.redis_url, ttl)

    result = memory_sets.get(ttl)
    if result is None:
        result = InMemoryTTLSet(ttl_seconds=float(ttl))
        memory_sets[ttl] = result
    return result


def ticket_lock_store(
    settings: Settings,
    *,
    redis_stores: dict[tuple[str, int, str | None], RedisDeliveryIdStore],
    ttl_seconds: int,
    prefix: str,
) -> RedisDeliveryIdStore | None:
    if is_shutting_down():
        return None
    backend = (settings.workflow.idempotency_backend or "memory").strip().lower()
    if backend == "redis" and settings.workflow.redis_url:
        return redis_store(
            redis_stores,
            settings.workflow.redis_url,
            ttl_seconds,
            prefix=prefix,
        )
    return None
