from runtime.cache import SuccessCache
from runtime.config import CACHE_CAPACITY, CACHE_TTL_SECONDS


def test_cache_expiration_and_lru_capacity():
    now = [0.0]
    cache = SuccessCache(clock=lambda: now[0])
    for number in range(CACHE_CAPACITY):
        cache.put(str(number), number)
    assert cache.get("0") == 0
    cache.put("new", 999)
    assert cache.get("1") is None
    assert cache.get("0") == 0
    now[0] = CACHE_TTL_SECONDS
    assert cache.get("0") is None
    assert cache.get("new") is None
    cache.put("again", 1)
    assert cache.get("again") == 1
    cache.clear()
    assert cache.get("again") is None
