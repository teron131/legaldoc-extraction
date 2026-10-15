"""Carry a background run's cooperative stop signal across document workers."""

import asyncio
from collections.abc import Awaitable
from contextvars import ContextVar
from threading import Event


class RunStopped(Exception):
    """End a cancelled workflow without treating cancellation as a failed document."""


stop_signal: ContextVar[Event | None] = ContextVar("stop_signal", default=None)


def check_stop() -> None:
    """Prevent queued documents and subsequent stages from starting after cancellation."""
    signal = stop_signal.get()
    if signal is not None and signal.is_set():
        raise RunStopped()


async def until_stopped[T](operation: Awaitable[T]) -> T:
    """Cancel an active agent loop and await its cleanup before its worker exits."""
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
