"""Service-loop-owned, bounded RAM caches; keys and values are never logged."""

from collections import OrderedDict
from collections.abc import Callable
from time import monotonic

from runtime.config import CACHE_CAPACITY, CACHE_TTL_SECONDS


class SuccessCache[T]:
    """Expire from successful completion, with bounded least-recently-used eviction."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires, value = entry
        if self._clock() >= expires:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: T) -> None:
        self._entries[key] = (self._clock() + CACHE_TTL_SECONDS, value)
        self._entries.move_to_end(key)
        while len(self._entries) > CACHE_CAPACITY:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
