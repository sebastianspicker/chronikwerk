"""Exercise replay expiration and capacity without wall-clock sleeps."""

from chronikwerk.operations.idempotency import InMemoryTTLSet


def test_claim_expiry_and_capacity_preserve_unexpired_deliveries() -> None:
    """Claims expire after the TTL and capacity never evicts live deliveries."""
    now = [0.0]
    store = InMemoryTTLSet(ttl_seconds=2, max_entries=2, now=lambda: now[0])

    assert store.try_claim("a")
    now[0] = 1
    assert store.try_claim("b")
    assert not store.try_claim("a")
    assert not store.try_claim("c")
    assert store.seen("a")
    now[0] = 2
    assert not store.seen("a")
    assert store.try_claim("c")
    assert not store.try_claim("b")
    now[0] = 4
    store.evict_expired()
    assert len(store) == 0
    assert store.try_claim("a")


def test_capacity_reclaims_expired_entries_before_periodic_sweep() -> None:
    """A full store reclaims expired keys before rejecting a new claim."""
    now = [0.0]
    store = InMemoryTTLSet(ttl_seconds=0.1, max_entries=1, now=lambda: now[0])

    assert store.add("old")
    now[0] = 0.1
    assert store.try_claim("new")
    assert not store.seen("old")
    assert store.seen("new")
