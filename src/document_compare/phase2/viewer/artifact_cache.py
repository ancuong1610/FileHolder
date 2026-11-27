"""Process-local cache for parsed, read-only artifacts of completed jobs."""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

_DEFAULT_JOBS = 2
_ENTRIES_PER_JOB = 6
_cache: OrderedDict[tuple[str, int, int], Any] = OrderedDict()
_lock = threading.Lock()


def _capacity() -> int:
    try:
        jobs = int(os.getenv("CRR_ARTIFACT_CACHE_JOBS", str(_DEFAULT_JOBS)))
    except ValueError:
        jobs = _DEFAULT_JOBS
    return _ENTRIES_PER_JOB * (jobs if jobs >= 0 else _DEFAULT_JOBS)


def _evict(capacity: int) -> None:
    while len(_cache) > capacity:
        _cache.popitem(last=False)


def load_json(path: Path) -> Any:
    """Load a JSON artifact. Callers must treat the returned object as read-only."""
    capacity = _capacity()
    if capacity == 0:
        return json.loads(path.read_text(encoding="utf-8"))

    path = Path(os.path.realpath(path, strict=True))
    stat = os.stat(path)
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            _evict(capacity)
            return _cache[key]
        _evict(capacity)

    data = json.loads(path.read_text(encoding="utf-8"))
    with _lock:
        for old_key in list(_cache):
            if old_key[0] == key[0] and old_key != key:
                del _cache[old_key]
        data = _cache.setdefault(key, data)
        _cache.move_to_end(key)
        _evict(capacity)
    return data


def clear() -> None:
    """Clear cached artifacts between tests."""
    with _lock:
        _cache.clear()
