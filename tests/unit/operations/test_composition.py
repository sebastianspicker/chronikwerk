"""Verify runtime ownership is assembled once at the composition boundary."""

import asyncio

from chronikwerk import composition
from tests.support.settings_factory import make_settings


def _install_fakes(monkeypatch, settings, clients: list, processors: list) -> None:
    """Replace configuration loading, logging, the client, and the processor factory."""

    class Client:
        def __init__(self, *, connection):
            assert connection == settings.zammad_connection
            self.closed = False
            clients.append(self)

        async def aclose(self):
            self.closed = True

    def processor(options, *, client, guards, history):
        processors.append((client, guards, history))

        async def process(_job):
            pass

        return process

    monkeypatch.setattr(composition, "load_settings", lambda: settings)
    monkeypatch.setattr(composition, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(composition, "AsyncZammadClient", Client)
    monkeypatch.setattr(composition, "build_ticket_processor", processor)


def test_runtime_injects_one_client_and_lifespan_closes_it(monkeypatch, tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    clients: list = []
    processors: list = []
    _install_fakes(monkeypatch, settings, clients, processors)

    configured, app = composition.build_runtime_application()
    assert configured is settings
    assert len(clients) == 1
    assert [client for client, _guards, _history in processors] == clients
    assert processors[0][2] is app.state.history
    assert app.state.scheduler.accepting

    async def scenario():
        async with app.router.lifespan_context(app):
            assert not clients[0].closed
        assert clients[0].closed
        assert not app.state.scheduler.accepting

    asyncio.run(scenario())


def test_independent_runtimes_do_not_share_history_or_guards(monkeypatch, tmp_path) -> None:
    """Two applications in one process keep separate volatile state."""
    settings = make_settings(str(tmp_path))
    processors: list = []
    _install_fakes(monkeypatch, settings, [], processors)

    _, first = composition.build_runtime_application()
    _, second = composition.build_runtime_application()
    (_, first_guards, first_history), (_, second_guards, second_history) = processors

    first_history.record("processed", 7)
    assert first_guards.try_acquire_ticket(7)
    assert first_guards.try_claim_delivery("delivery-7")

    assert first.state.history is first_history
    assert second.state.history is second_history
    assert second_history.read(10) == []
    assert second_guards.try_acquire_ticket(7)
    assert second_guards.try_claim_delivery("delivery-7")
    assert first.state.scheduler is not second.state.scheduler


def test_tsa_username_is_unwrapped_only_at_the_composition_boundary(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "signing": {"timestamp": {"rfc3161": {"user": "tsa-user", "password": "tsa-password"}}}
        },
    )

    options = composition._archive_runtime_options(settings)  # pylint: disable=protected-access

    configured_user = settings.signing.timestamp.rfc3161.user
    assert configured_user is not None
    assert configured_user.get_secret_value() == "tsa-user"
    assert options.documents.signing.timestamp.user == "tsa-user"
    assert options.documents.signing.timestamp.password == "tsa-password"
