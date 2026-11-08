"""The adapter loads only explicit local code and preserves sync/async behavior."""
import asyncio
import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

from integration import entrypoint
from integration.entrypoint import EntrypointError, invoke, resolve_entrypoint, validate_callable


def valid_callback(query, *, store, run_id, on_event):
    return {"query": query, "run_id": run_id}


def install_module(monkeypatch, callback=valid_callback):
    module = ModuleType("test_local_orchestrator")
    module.run_query = callback
    module.nested = SimpleNamespace(run_query=callback)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


@pytest.mark.parametrize("attribute", ["run_query", "nested.run_query"])
def test_explicit_attributes_resolve_without_invoking(monkeypatch, attribute):
    calls = []

    def callback(query, *, store, run_id, on_event):
        calls.append(query)
        return {"query": query}

    module = install_module(monkeypatch, callback)
    validate_callable(callback)
    assert resolve_entrypoint(f"{module.__name__}:{attribute}") is callback
    assert calls == []


@pytest.mark.parametrize("spec", [
    None, "", "module", "module:", ":run", "module:run:other", "module:run()",
    "module:__import__('os')", "module..child:run", "module:child..run", ".module:run",
])
def test_invalid_specs_fail_before_import(monkeypatch, spec):
    def unexpected_import(name):
        pytest.fail(f"Invalid entrypoint attempted import: {name}")

    monkeypatch.setattr(entrypoint.importlib, "import_module", unexpected_import)
    with pytest.raises(EntrypointError):
        resolve_entrypoint(spec)


@pytest.mark.parametrize("missing", ["selected", "selected.module"])
def test_absent_selected_module_is_reported_as_unavailable(monkeypatch, missing):
    original = ModuleNotFoundError("selected module absent", name=missing)

    def absent(name):
        raise original

    monkeypatch.setattr(entrypoint.importlib, "import_module", absent)
    with pytest.raises(EntrypointError, match="not available") as caught:
        resolve_entrypoint("selected.module:run_query")
    assert caught.value.__cause__ is original


def test_missing_internal_dependency_preserves_its_original_error(monkeypatch):
    original = ModuleNotFoundError("agent dependency absent", name="missing_agent_dependency")

    def broken_import(name):
        raise original

    monkeypatch.setattr(entrypoint.importlib, "import_module", broken_import)
    with pytest.raises(ModuleNotFoundError) as caught:
        resolve_entrypoint("selected.module:run_query")
    assert caught.value is original


def test_missing_attribute_and_noncallable_are_rejected(monkeypatch):
    module = install_module(monkeypatch)
    with pytest.raises(EntrypointError, match="not found"):
        resolve_entrypoint(f"{module.__name__}:absent.run_query")
    module.run_query = 17
    with pytest.raises(EntrypointError, match="callable"):
        resolve_entrypoint(f"{module.__name__}:run_query")


@pytest.mark.parametrize("callback", [
    None,
    lambda query: None,
    lambda *, query, store, run_id, on_event: None,
    lambda query, *, store, run_id, on_event, required_extra: None,
    str,
])
def test_incompatible_or_uninspectable_signatures_are_rejected(callback):
    with pytest.raises(EntrypointError):
        validate_callable(callback)


def test_variadic_and_bound_callbacks_can_follow_the_contract():
    validate_callable(lambda *args, **kwargs: None)

    class Agent:
        def run(self, query, *, store, run_id, on_event):
            return query

    validate_callable(Agent().run)


def test_sync_invoke_passes_objects_unchanged_and_returns_raw_output():
    expected = {"result": object()}
    database = object()
    events = []
    event_callback = lambda kind, payload: events.append((kind, payload))

    def callback(query, *, store, run_id, on_event):
        assert query == "  original query  "
        assert store is database
        assert run_id == "supplied-run"
        assert on_event is event_callback
        on_event("progress", {"step": "actual callback"})
        return expected

    result = invoke(callback, "  original query  ", store=database, run_id="supplied-run", on_event=event_callback)
    assert result is expected
    assert events == [("progress", {"step": "actual callback"})]


def test_async_invoke_waits_for_callback_completion():
    events = []

    async def callback(query, *, store, run_id, on_event):
        await asyncio.sleep(0)
        on_event("progress", {"query": query})
        return {"run_id": run_id}

    result = invoke(callback, "async query", store=object(), run_id="async-run", on_event=lambda *event: events.append(event))
    assert result == {"run_id": "async-run"}
    assert events == [("progress", {"query": "async query"})]


def test_generic_awaitable_return_is_supported():
    expected = {"actual": "awaited result"}

    class AwaitableResult:
        def __await__(self):
            async def complete():
                await asyncio.sleep(0)
                return expected

            return complete().__await__()

    def callback(query, *, store, run_id, on_event):
        return AwaitableResult()

    assert invoke(callback, "query", store=object(), run_id="run", on_event=lambda *_: None) is expected


@pytest.mark.parametrize("async_callback", [False, True])
def test_callback_exceptions_are_not_wrapped_or_replaced(async_callback):
    original = RuntimeError("Original orchestrator failure.")

    def sync(query, *, store, run_id, on_event):
        raise original

    async def async_handler(query, *, store, run_id, on_event):
        await asyncio.sleep(0)
        raise original

    with pytest.raises(RuntimeError) as caught:
        invoke(async_handler if async_callback else sync, "query", store=object(), run_id="run", on_event=lambda *_: None)
    assert caught.value is original


def test_nested_event_loop_is_reported_without_leaking_an_unawaited_coroutine():
    created = []

    async def complete():
        return "result"

    def callback(query, *, store, run_id, on_event):
        result = complete()
        created.append(result)
        return result

    async def already_async():
        with pytest.raises(EntrypointError, match="running event loop"):
            invoke(callback, "query", store=object(), run_id="run", on_event=lambda *_: None)

    asyncio.run(already_async())
    assert inspect.getcoroutinestate(created[0]) == inspect.CORO_CLOSED
