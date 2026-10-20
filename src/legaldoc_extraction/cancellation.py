"""Carry a background run's cooperative stop signal across document workers."""

import asyncio
from collections.abc import Awaitable
from contextvars import ContextVar
from threading import Event


class RunStopped(Exception):
    """End a cancelled workflow without treating cancellation as a failed document."""


# Worker threads see the signal only when submitted under copy_context().
stop_signal: ContextVar[Event | None] = ContextVar("stop_signal", default=None)


def check_stop() -> None:
    """Raise RunStopped once this run's stop signal is set, so queued documents and later stages never start."""
    signal = stop_signal.get()
    if signal is not None and signal.is_set():
        raise RunStopped()


async def until_stopped[T](operation: Awaitable[T]) -> T:
    """Await the operation while polling the stop signal; on stop, cancel it and wait for its cleanup before raising RunStopped."""
    task = asyncio.ensure_future(operation)
    try:
        while not task.done():
            check_stop()
            await asyncio.wait({task}, timeout=0.1)
        check_stop()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
