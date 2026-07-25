from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_background_tasks: set[asyncio.Task[Any]] = set()


def fire_and_forget(coro: Coroutine[Any, Any, Any]) -> None:
    """Schedules coro without awaiting it, keeping a strong reference until it
    finishes (asyncio only holds a weak ref to tasks, so an unreferenced task
    can be GC'd mid-flight). Use for work whose outcome the caller doesn't need
    to wait on (e.g. best-effort notification emails), so response time
    doesn't depend on it."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
