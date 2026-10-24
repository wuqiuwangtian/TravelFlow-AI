"""单进程本地服务中，防止对话与活动编辑同时覆盖同一分支。"""

import asyncio
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from app.core.errors import BranchConflict

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def branch_busy(branch_id: str) -> bool:
    """只读查看分支是否正在更新，不获取或创建锁。"""
    lock = _locks.get(branch_id)
    return lock is not None and lock.locked()


@asynccontextmanager
async def branch_guard(branch_id: str, *, wait: bool = False):
    lock = _locks.setdefault(branch_id, asyncio.Lock())
    if lock.locked() and not wait:
        raise BranchConflict("该方案正在生成或更新，请完成后再试")
    async with lock:
        yield
