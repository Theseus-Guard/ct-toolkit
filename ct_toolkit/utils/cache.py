"""
ct_toolkit.utils.cache
----------------------
A small LRU cache for read results that depend on the provenance data. An entry is valid while the vault's
generation counter is unchanged (it changes on every write and rollback made through the vault) and it is
younger than `max_age_s`. The age limit covers edits made directly in the database, which do not change the
counter, so such an edit shows within `max_age_s` seconds at the latest.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable, TypeVar

T = TypeVar("T")
_MISSING = object()


class GenerationCache:
    def __init__(self, maxsize: int = 64, max_age_s: float = 30.0) -> None:
        self._maxsize = maxsize
        self._max_age_s = max_age_s
        self._data: OrderedDict[Hashable, tuple[int, float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_compute(self, key: Hashable, generation: int, compute: Callable[[], T]) -> T:
        """Returns the cached value for `key` if it was computed for `generation` and is still fresh; otherwise
        computes it. Callers pass the generation they read BEFORE computing, so a write during the computation
        leaves the entry stale, and the next call recomputes it."""
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is not None and item[0] == generation and now - item[1] <= self._max_age_s:
                self._data.move_to_end(key)
                return item[2]
        value = compute()
        with self._lock:
            self._data[key] = (generation, time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
