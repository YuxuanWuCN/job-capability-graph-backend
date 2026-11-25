"""图谱只读结果的内存短缓存。

为什么需要：`GET /api/v1/graph` 返回的是**全站同一份**已发布图谱，但每个请求都要走
一次 Postgres 取版本 + 若干次 Neo4j 查询 + 归一化 + 6KB JSON 序列化。50 并发实测把
2 核打满、p95 到 3 秒。缓存几秒钟后，并发峰值下同一份结果只算一次。

一致性：图谱发布（`publish_graph_version`）成功后调用 `invalidate_graph_cache()`
立刻失效，因此不存在「发布完要等 TTL」的问题。
"""

from __future__ import annotations

import time
from typing import Any

# 5 秒：足够吸收「几百人同时打开图谱页」的峰值，又不会让数据看起来是旧的。
GRAPH_CACHE_TTL_SECONDS = 5.0

_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def get_cached(key: tuple[Any, ...]) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > GRAPH_CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def set_cached(key: tuple[Any, ...], value: Any) -> None:
    # 参数组合（domain + 两个上限）个位数，不做淘汰也不会涨。
    _cache[key] = (time.monotonic(), value)


def invalidate_graph_cache() -> None:
    _cache.clear()
