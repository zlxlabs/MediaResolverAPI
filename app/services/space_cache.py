"""X Space 物化缓存：文件系统即状态的两阶段交付（design「交付形态 v2」）。

目录内容即状态（锁定决策 1，无任务队列/DB）：
  {SPACE_CACHE_DIR}/{space_id}.part   获取中；崩溃遗留由启动扫描回收
  {SPACE_CACHE_DIR}/{space_id}.m4a    final；``os.replace`` 改名即「完整」态
进程内 per-space 单飞（dict[space_id, asyncio.Task]，锁定决策 3）；失败态不持久，
被 GET 消费一次（502/400/404）后由下次 GET 重新 spawn（锁定决策 4）。
TTL 72h + 磁盘水位淘汰见 :func:`sweep`（锁定决策 7）。
"""

from __future__ import annotations

import asyncio
import enum
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Coroutine, Optional

from loguru import logger

from ..core.config import settings

FINAL_SUFFIX = ".m4a"
PART_SUFFIX = ".part"

# 常驻周期清理间隔（review F2：sweep 不能只在首次使用跑一次，长期运行进程必须周期回收）。
_SWEEP_INTERVAL_SECONDS = 3600.0
# 启动扫描的孤儿回收宽限期（review F1 廉价防护）：新于该值的 .part 不回收，
# 避免多进程误部署时删掉他进程在制品；真部署单进程（Dockerfile CMD 无 --workers）。
_STALE_PART_GRACE_SECONDS = 600.0

# 惰性互斥锁与单飞登记表（与 stream.StreamLimiter 同一 lazy 模式：锁在首次使用的
# 事件循环创建；测试与多个 TestClient portal loop 复用时仅在争用路径才触碰循环绑定）。
_tasks: dict[str, asyncio.Task] = {}
_lock: Optional[asyncio.Lock] = None
_started = False
_sweeper_task: Optional[asyncio.Task] = None


class SpaceCacheStatus(enum.Enum):
    FINAL = "final"
    EXTRACTING = "extracting"
    FAILED = "failed"
    ABSENT = "absent"


def cache_dir() -> Path:
    return Path(settings.SPACE_CACHE_DIR)


def final_path(space_id: str) -> Path:
    return cache_dir() / f"{space_id}{FINAL_SUFFIX}"


def part_path(space_id: str) -> Path:
    return cache_dir() / f"{space_id}{PART_SUFFIX}"


def ensure_dir() -> None:
    cache_dir().mkdir(parents=True, exist_ok=True)


def _lock_for_loop() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def status(space_id: str) -> SpaceCacheStatus:
    """文件系统 + 进程内单飞表的综合状态。FINAL 优先（final 恒为已校验完整物）。"""
    if final_path(space_id).is_file():
        return SpaceCacheStatus.FINAL
    task = _tasks.get(space_id)
    if task is None:
        return SpaceCacheStatus.ABSENT
    if not task.done():
        return SpaceCacheStatus.EXTRACTING
    if task.cancelled() or task.exception() is not None:
        return SpaceCacheStatus.FAILED
    # 成功但 final 已被 TTL/水位清掉（罕见竞态）：按未命中处理，下次 GET 重拉
    return SpaceCacheStatus.ABSENT


def consume_failure(space_id: str) -> Optional[BaseException]:
    """弹出已失败任务的异常（消费一次）；无失败条目返回 None。

    失败态不持久：消费后状态回落 ABSENT，下次 GET 重新 spawn（锁定决策 4）。
    """
    task = _tasks.pop(space_id, None)
    if task is None:
        return None
    if task.cancelled():
        return asyncio.CancelledError("materialization task cancelled")
    return task.exception()


def _make_done_callback(space_id: str) -> Callable[[asyncio.Task], None]:
    def _on_done(task: asyncio.Task) -> None:
        if task.cancelled():
            _tasks.pop(space_id, None)
            return
        # 取走异常，避免 "Task exception was never retrieved"；失败条目保留给 GET 消费
        exc = task.exception()
        if exc is None and _tasks.get(space_id) is task:
            _tasks.pop(space_id, None)

    return _on_done


async def start(space_id: str, factory: Callable[[], Coroutine[None, None, None]]) -> bool:
    """单飞 spawn：已有进行中任务时不重复 spawn（返回 False）。

    spawn 前确保缓存目录存在（任务体写 .part 的前置）。
    """
    ensure_dir()
    async with _lock_for_loop():
        task = _tasks.get(space_id)
        if task is not None and not task.done():
            return False
        _tasks.pop(space_id, None)  # 成功旧条目（final 已被清）或垃圾残条目
        created = asyncio.create_task(factory())
        _tasks[space_id] = created
        created.add_done_callback(_make_done_callback(space_id))
        return True


def publish_final(space_id: str) -> Path:
    """校验齐备后的原子发布：``.part`` --os.replace--> ``.m4a``（锁定决策 1/5）。

    同目录内改名保证同一文件系统（os.replace 原子性的前提）。缺 .part 说明产物
    文件被异常清理，fail-loud 上抛，绝不产 final。
    """
    part = part_path(space_id)
    final = final_path(space_id)
    if not part.is_file():
        raise FileNotFoundError(f"materialized part file missing: {part.name}")
    os.replace(part, final)
    return final


def discard_part(space_id: str) -> None:
    """任务失败收尾：删除 .part（故障矩阵：磁盘满/段失败只留可回收临时文件）。"""
    try:
        part_path(space_id).unlink()
    except FileNotFoundError:
        pass


def startup_scan() -> int:
    """崩溃遗留 .part 全量回收（锁定决策 6：重启即弃，客户端重试重新拉取）。

    只收陈旧件：新于 ``_STALE_PART_GRACE_SECONDS`` 的 .part 跳过（review F1）——
    契约外多进程误部署时不删他进程在制品；本进程正常路径无新鲜 .part 可见
    （扫描仅在进程首次使用时跑，刚重启的进程自己还没产出过 .part）。
    """
    directory = cache_dir()
    if not directory.is_dir():
        return 0
    removed = 0
    now = time.time()
    for part in directory.glob(f"*{PART_SUFFIX}"):
        try:
            if now - part.stat().st_mtime < _STALE_PART_GRACE_SECONDS:
                continue
            part.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    if removed:
        logger.info("twitter_space cache startup scan removed {} stale .part file(s)", removed)
    return removed


async def _sweep_loop() -> None:
    """常驻周期清理（review F2）：首轮 ensure_started 已扫，此后每周期一轮 sweep。

    单轮失败记 error 不静默，也不杀进程/循环——磁盘竞态等暂时性失败下一周期重试。
    """
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
        try:
            stats = sweep()
        except Exception as exc:
            logger.error("twitter_space cache periodic sweep failed err={}", type(exc).__name__)
            continue
        if stats["ttl_removed"] or stats["watermark_removed"]:
            logger.info("twitter_space cache periodic sweep {}", stats)


def ensure_started() -> None:
    """惰性启动扫描 + 常驻周期清理的 spawn 点（仅首个调用者生效）。

    main.py lifespan 不在本卡修改边界内的等价替代：任何 spawn 必经端点（端点先调
    本函数），故不存在「扫描晚于本进程产出 .part」的窗口。``_sweep_loop`` 需要
    运行中的事件循环，从 async 端点首次进入时 spawn；无 loop 的调用在此 fail-fast
    上抛（生产无此路径，调用方只有 async 端点）。
    """
    global _started, _sweeper_task
    if _started:
        return
    _started = True
    ensure_dir()
    startup_scan()
    sweep()
    _sweeper_task = asyncio.get_running_loop().create_task(_sweep_loop())


def _disk_free_bytes() -> int:
    return shutil.disk_usage(cache_dir()).free


def _unlink(path: Path) -> None:
    """删除 final 的统一入口。与交付/读取并发时依赖 POSIX unlink-while-open：
    读者的 fd 继续读完整个文件，目录项即刻消失、新 GET 走未命中重拉
    （锁定决策 6「清理跳过正被交付的 final」的落地语义：无需检测打开状态，
    unlink 本身对读者安全；见 tests/test_space_cache.py unlink-while-open 用例）。
    """
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def sweep() -> dict[str, int]:
    """TTL 过期删除 + 磁盘水位淘汰（锁定决策 7）。返回统计供测试与日志消费。"""
    stats = {"ttl_removed": 0, "watermark_removed": 0}
    directory = cache_dir()
    if not directory.is_dir():
        return stats
    ttl_seconds = settings.SPACE_CACHE_TTL_HOURS * 3600
    now = time.time()
    for final in sorted(directory.glob(f"*{FINAL_SUFFIX}")):
        try:
            age = now - final.stat().st_mtime
        except FileNotFoundError:
            continue
        if age >= ttl_seconds:
            _unlink(final)
            stats["ttl_removed"] += 1
    _evict_to_watermark(stats)
    return stats


def _evict_to_watermark(stats: dict[str, int]) -> None:
    min_free_bytes = settings.SPACE_CACHE_MIN_FREE_MB * 1024 * 1024
    if settings.SPACE_CACHE_MIN_FREE_MB <= 0 or _disk_free_bytes() >= min_free_bytes:
        return
    directory = cache_dir()
    candidates = sorted(directory.glob(f"*{FINAL_SUFFIX}"), key=_mtime_of)
    for final in candidates:
        if _disk_free_bytes() >= min_free_bytes:
            break
        logger.warning(
            "twitter_space cache below watermark, evicting oldest final name={}",
            final.name,
        )
        _unlink(final)
        stats["watermark_removed"] += 1


def _mtime_of(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0
