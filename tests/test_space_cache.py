"""X Space 物化缓存状态机（交付形态 v2 锁定决策 1/5/6/7）。

文件系统即状态：{cache}/{id}.part --os.replace--> {cache}/{id}.m4a（改名即完整态）；
进程内 per-space 单飞；失败态不持久（GET 消费一次后重新 spawn）；
TTL 72h + 磁盘水位淘汰；崩溃遗留 .part 由启动扫描回收。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import namedtuple
from pathlib import Path

import pytest
from loguru import logger

import app.services.space_cache as space_cache
from app.core.config import settings

SPACE_ID = "1AKEmvYqbrOKL"


@pytest.fixture(autouse=True)
def _reset_space_cache(monkeypatch, tmp_path):
    """模块级单飞状态逐测试清零；缓存目录指到本测试的 tmp_path。"""
    monkeypatch.setattr(settings, "SPACE_CACHE_DIR", str(tmp_path / "space-cache"))
    space_cache._tasks.clear()
    monkeypatch.setattr(space_cache, "_started", False)
    monkeypatch.setattr(space_cache, "_lock", None)
    monkeypatch.setattr(space_cache, "_sweeper_task", None, raising=False)
    yield
    space_cache._tasks.clear()
    space_cache._sweeper_task = None


def _make_final(space_id: str, payload: bytes = b"final-bytes") -> Path:
    space_cache.ensure_dir()
    final = space_cache.final_path(space_id)
    final.write_bytes(payload)
    return final


def _make_part(space_id: str, payload: bytes = b"partial") -> Path:
    space_cache.ensure_dir()
    part = space_cache.part_path(space_id)
    part.write_bytes(payload)
    return part


def _age(path: Path, seconds: float) -> None:
    """把文件 mtime 拨老 seconds 秒（造陈旧件，无需可控时钟）。"""
    now = time.time()
    os.utime(path, (now - seconds, now - seconds))


async def _cancel_sweeper() -> None:
    """测试收尾：取消本 loop spawn 的常驻清理任务，不留跨测试活任务。"""
    task = space_cache._sweeper_task
    space_cache._sweeper_task = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _FakeDiskUsage:
    """按调用次序吐 free 值（最后一个值常驻），记录调用。"""

    _usage = namedtuple("usage", "total used free")

    def __init__(self, free_values: list[int]):
        self.values = list(free_values)
        self.calls: list[str] = []

    def __call__(self, path):
        self.calls.append(str(path))
        free = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return self._usage(total=10**12, used=0, free=free)


class TestPathsAndPublish:
    def test_publish_is_atomic_rename_part_gone_final_intact(self):
        """os.replace 发布：part 消失、final 内容完整（缺改名=直接写 final 时此测试转红）。"""
        part = _make_part(SPACE_ID, b"complete-materialized-bytes")
        published = space_cache.publish_final(SPACE_ID)
        assert published == space_cache.final_path(SPACE_ID)
        assert published.read_bytes() == b"complete-materialized-bytes"
        assert not part.exists()
        assert space_cache.status(SPACE_ID) is space_cache.SpaceCacheStatus.FINAL

    def test_publish_without_part_fails_loud_no_final(self):
        with pytest.raises(FileNotFoundError):
            space_cache.publish_final(SPACE_ID)
        assert not space_cache.final_path(SPACE_ID).exists()

    async def test_materialization_lifecycle_part_during_final_after(self):
        """获取期间只有 .part（final 绝不提前出现）；发布后 .part 消失。"""
        seen: dict[str, Path] = {}

        async def runner():
            seen["part"] = space_cache.part_path(SPACE_ID)
            seen["part"].write_bytes(b"mid-flight")
            assert not space_cache.final_path(SPACE_ID).exists()
            space_cache.publish_final(SPACE_ID)

        assert space_cache.status(SPACE_ID) is space_cache.SpaceCacheStatus.ABSENT
        await space_cache.start(SPACE_ID, runner)
        await space_cache._tasks[SPACE_ID]
        assert space_cache.status(SPACE_ID) is space_cache.SpaceCacheStatus.FINAL
        assert seen["part"].exists() is False

    async def test_start_ensures_cache_dir_exists(self):
        async def noop():
            pass

        assert not space_cache.cache_dir().exists()
        await space_cache.start(SPACE_ID, noop)
        assert space_cache.cache_dir().is_dir()


class TestSingleFlight:
    async def test_concurrent_starts_spawn_once(self):
        calls = 0
        release = asyncio.Event()

        async def runner():
            nonlocal calls
            calls += 1
            await release.wait()

        await asyncio.gather(
            space_cache.start(SPACE_ID, runner),
            space_cache.start(SPACE_ID, runner),
            space_cache.start(SPACE_ID, runner),
        )
        assert calls == 1
        release.set()

    async def test_failed_entry_consumed_once_then_respawn_allowed(self):
        async def boom():
            raise RuntimeError("proxy exploded")

        await space_cache.start(SPACE_ID, boom)
        task = space_cache._tasks[SPACE_ID]
        with pytest.raises(RuntimeError):
            await task
        assert space_cache.status(SPACE_ID) is space_cache.SpaceCacheStatus.FAILED
        exc = space_cache.consume_failure(SPACE_ID)
        assert isinstance(exc, RuntimeError)
        assert space_cache.consume_failure(SPACE_ID) is None
        assert space_cache.status(SPACE_ID) is space_cache.SpaceCacheStatus.ABSENT

    async def test_success_entry_released_dict_not_kept(self):
        async def ok():
            space_cache.ensure_dir()
            space_cache.part_path(SPACE_ID).write_bytes(b"x")
            space_cache.publish_final(SPACE_ID)

        await space_cache.start(SPACE_ID, ok)
        await asyncio.sleep(0.05)
        assert SPACE_ID not in space_cache._tasks
        assert space_cache.status(SPACE_ID) is space_cache.SpaceCacheStatus.FINAL

    async def test_restart_reset_is_startup_part_scan(self):
        """重启后单飞失效：崩溃遗留 .part 被启动扫描回收即重置（锁定决策 6）。"""
        stale = _make_part(SPACE_ID)
        _age(stale, space_cache._STALE_PART_GRACE_SECONDS + 60)
        removed = space_cache.startup_scan()
        assert removed >= 1
        assert not stale.exists()
        assert space_cache.status(SPACE_ID) is space_cache.SpaceCacheStatus.ABSENT


class TestStartupScanGrace:
    """孤儿回收只收陈旧 .part：新于宽限期的视为可能的在制品，不碰（review F1）。"""

    def test_startup_scan_keeps_fresh_part_removes_stale(self):
        fresh = _make_part("1J" + "j" * 11)  # 刚写即新鲜
        stale = _make_part("1K" + "k" * 11)
        _age(stale, space_cache._STALE_PART_GRACE_SECONDS + 60)
        removed = space_cache.startup_scan()
        assert removed == 1
        assert fresh.exists()
        assert not stale.exists()

    def test_fresh_part_ageing_into_grace_window_gets_collected_later(self):
        """宽限期是相对 mtime 的，不是一次性标记：老化出宽限期后照常回收。"""
        part = _make_part(SPACE_ID)
        _age(part, space_cache._STALE_PART_GRACE_SECONDS + 1)
        assert space_cache.startup_scan() == 1
        assert not part.exists()


class TestFaultMatrix:
    async def test_runner_failure_discards_part_no_final(self, monkeypatch):
        """任何失败（此处以段失败语义注入）：.part 删除、不产 final（fail-loud）。"""

        async def failing_runner():
            space_cache.ensure_dir()
            space_cache.part_path(SPACE_ID).write_bytes(b"half")
            raise RuntimeError("segment fetch failed: segment HTTP 404")

        await space_cache.start(SPACE_ID, failing_runner)
        task = space_cache._tasks[SPACE_ID]
        with pytest.raises(RuntimeError):
            await task
        space_cache.discard_part(SPACE_ID)
        assert not space_cache.part_path(SPACE_ID).exists()
        assert not space_cache.final_path(SPACE_ID).exists()


class TestSweep:
    def test_ttl_expiry_removes_only_expired_finals(self, monkeypatch):
        fresh = _make_final("1A" + "a" * 11)
        expired = _make_final("1B" + "b" * 11)
        now = time.time()
        ttl = settings.SPACE_CACHE_TTL_HOURS * 3600
        os.utime(fresh, (now - ttl + 3600, now - ttl + 3600))
        os.utime(expired, (now - ttl - 60, now - ttl - 60))
        stats = space_cache.sweep()
        assert stats["ttl_removed"] == 1
        assert fresh.exists()
        assert not expired.exists()

    def test_watermark_evicts_oldest_first_until_recovered(self, monkeypatch):
        old = _make_final("1C" + "c" * 11)
        mid = _make_final("1D" + "d" * 11)
        new = _make_final("1E" + "e" * 11)
        now = time.time()
        for path, age in ((old, 3000), (mid, 2000), (new, 1000)):
            os.utime(path, (now - age, now - age))
        # 空闲检查序列：闸门(100MB，低于水位) → old 淘汰前(100MB，仍不足) → old 淘汰后(3000MB，回升即停)
        fake = _FakeDiskUsage(free_values=[100 * 1024**2, 100 * 1024**2, 3000 * 1024**2])
        monkeypatch.setattr(space_cache, "_disk_free_bytes", lambda: fake(".").free)
        monkeypatch.setattr(settings, "SPACE_CACHE_MIN_FREE_MB", 2048)
        stats = space_cache.sweep()
        assert stats["watermark_removed"] == 1
        assert not old.exists()
        assert mid.exists() and new.exists()

    def test_sweep_without_cache_dir_is_noop(self, monkeypatch):
        monkeypatch.setattr(settings, "SPACE_CACHE_DIR", "/nonexistent/space-cache-x")
        assert space_cache.sweep() == {"ttl_removed": 0, "watermark_removed": 0}

    def test_unlink_while_open_reader_finishes(self, monkeypatch):
        """清理与交付并发：POSIX unlink-while-open——读者 fd 继续读完（锁定决策 6/7）。"""
        final = _make_final(SPACE_ID, b"download-in-progress-bytes")
        with open(final, "rb") as reader:
            monkeypatch.setattr(settings, "SPACE_CACHE_TTL_HOURS", 0)
            stats = space_cache.sweep()
            assert stats["ttl_removed"] == 1
            assert not final.exists()
            assert reader.read() == b"download-in-progress-bytes"


class TestEnsureStarted:
    async def test_ensure_started_scan_once_and_sweep(self, monkeypatch):
        stale = _make_part("1F" + "f" * 11)
        _age(stale, space_cache._STALE_PART_GRACE_SECONDS + 60)
        space_cache.ensure_started()
        assert not stale.exists()
        # 幂等：第二次不再扫描（新遗留只在真实重启后出现，重启即新进程重扫）
        again = _make_part("1G" + "g" * 11)
        _age(again, space_cache._STALE_PART_GRACE_SECONDS + 60)
        space_cache.ensure_started()
        assert again.exists()
        await _cancel_sweeper()


class TestPeriodicSweeper:
    """常驻周期清理（review F2）：sweep 不再只在首次使用跑一次；周期异常不静默。"""

    async def test_periodic_sweep_removes_expired_final(self, monkeypatch):
        """长期运行进程：过期 final 在后续周期被清，新鲜 final 不动。
        可控节奏：monkeypatch 周期常量为 0.01s，真实 sweep 真删盘上文件。"""
        monkeypatch.setattr(space_cache, "_SWEEP_INTERVAL_SECONDS", 0.01)
        expired = _make_final("1H" + "h" * 11)
        fresh = _make_final("1I" + "i" * 11)
        _age(expired, settings.SPACE_CACHE_TTL_HOURS * 3600 + 60)
        space_cache.ensure_started()
        try:
            await asyncio.sleep(0.08)  # 覆盖多个周期
            assert not expired.exists()
            assert fresh.exists()
        finally:
            await _cancel_sweeper()

    async def test_sweep_failure_logs_error_and_loop_survives(self, monkeypatch):
        """单轮 sweep 抛错：logger.error 不静默；循环不死，下一周期继续跑。"""
        monkeypatch.setattr(space_cache, "_SWEEP_INTERVAL_SECONDS", 0.01)
        calls = {"n": 0}
        errors: list[str] = []

        def flaky_sweep() -> dict[str, int]:
            calls["n"] += 1
            if calls["n"] == 2:  # 第 1 次是 ensure_started 首轮同步 sweep；周期轮才注入失败
                raise OSError(28, "No space left on device")
            return {"ttl_removed": 0, "watermark_removed": 0}

        monkeypatch.setattr(space_cache, "sweep", flaky_sweep)
        sink_id = logger.add(
            lambda msg: errors.append(msg)
            if msg.record["level"].name == "ERROR"
            else None
        )
        space_cache.ensure_started()
        try:
            await asyncio.sleep(0.08)
            assert calls["n"] >= 2  # 异常后循环继续
            assert len(errors) >= 1  # 不静默：error 落日志
        finally:
            logger.remove(sink_id)
            await _cancel_sweeper()
