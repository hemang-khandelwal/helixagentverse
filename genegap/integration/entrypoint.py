"""Load an explicitly configured, trusted local orchestrator callback.

An entrypoint is selected by the person launching the interface, never by
retrieved text or model output. Importing it can execute normal Python module
initialization; signature validation itself never calls the callback.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from genegap.store import Store


class EntrypointError(ValueError):
    """The selected local entrypoint is absent or has an incompatible contract."""


def validate_callable(callback: Callable[..., Any]) -> None:
    """Check the integration calling convention without running a callback."""
    if not callable(callback):
        raise EntrypointError("The orchestrator entrypoint must be callable.")
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError) as exc:
        raise EntrypointError("The orchestrator signature cannot be inspected; provide a Python wrapper.") from exc
    try:
        signature.bind("query", store=object(), run_id="run-id", on_event=lambda _kind, _payload: None)
    except TypeError as exc:
        raise EntrypointError(
            "The orchestrator must accept query as a positional argument and "
            "store, run_id and on_event as keyword arguments, with no other required arguments."
        ) from exc


def resolve_entrypoint(spec: str) -> Callable[..., Any]:
    """Resolve ``module:attribute`` (including dotted attributes), never eval it.

    A missing selected module becomes EntrypointError. A missing dependency
    imported *inside* that module remains its original ModuleNotFoundError, so
    callers can distinguish unavailable agent code from a broken installation.
    """
    if not isinstance(spec, str) or spec.count(":") != 1:
        raise EntrypointError("Select an explicit orchestrator entrypoint as module:attribute.")
    module_name, attribute_path = spec.strip().split(":")
    if not all(
        part and all(component.isidentifier() for component in part.split("."))
        for part in (module_name, attribute_path)
    ):
        raise EntrypointError("Entrypoint module and attribute paths must contain dotted Python identifiers.")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name
        if missing and (module_name == missing or module_name.startswith(missing + ".")):
            raise EntrypointError(f"Orchestrator module {module_name!r} is not available in this checkout.") from exc
        raise
    callback = module
    try:
        for component in attribute_path.split("."):
            callback = getattr(callback, component)
    except AttributeError as exc:
        raise EntrypointError(f"Orchestrator attribute {attribute_path!r} was not found in {module_name!r}.") from exc
    validate_callable(callback)
    return callback


async def _await_result(result):
    # asyncio.run requires a coroutine even when the producer returned some
    # other object implementing the awaitable protocol.
    return await result


def invoke(
    callback: Callable[..., Any], query: str, *, store: Store,
    run_id: str, on_event: Callable[[str, dict], None],
) -> Any:
    """Invoke once, preserving the callback's output and original exceptions.

    Async results are awaited by asyncio.run for the synchronous terminal UI.
    A synchronous caller cannot nest this bridge in an already running event
    loop; an async application should await its orchestrator directly instead.
    """
    validate_callable(callback)
    result = callback(query, store=store, run_id=run_id, on_event=on_event)
    if not inspect.isawaitable(result):
        return result
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result(result))
    if inspect.iscoroutine(result):
        result.close()
    raise EntrypointError("An async orchestrator cannot use the terminal invoke bridge inside a running event loop.")
