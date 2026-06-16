#!/usr/bin/env python3
"""
抖音 TikHub 真实响应采集脚本（用于测试 fixtures）

对应设计文档 docs/douyin-fallback-design.md 的 #13（脱敏真实样本）。

把一个抖音分享链接 / 长链 / aweme_id 打到多级降级链涉及的各个 TikHub 端点，
原样保存响应到 tests/fixtures/douyin/，供单元/整合测试 mock 使用。

关键设计：
- 直接用 httpx，**不调 raise_for_status**，这样非 200 / 终态(私密/删除/版权)的
  错误响应体也能完整抓到（codex #4：生产里 raise_for_status 会吞掉 4xx body）。
- 短链先展开拿 aweme_id；hybrid 端点直接吃原始分享链接，不需要 aweme_id。

用法：
    # 正常视频（产出 web_v1/web_v2/app_v3/hybrid.json）
    .venv/bin/python scripts/collect_douyin_fixtures.py --url "https://v.douyin.com/-Q8et5ToUhs/"

    # 终态样本：换一个私密/已删除/版权受限的链接，并加前缀区分
    .venv/bin/python scripts/collect_douyin_fixtures.py --url "<私密视频链接>" --prefix "private_"
    .venv/bin/python scripts/collect_douyin_fixtures.py --aweme-id 123456 --prefix "deleted_"

环境：从 .env 读取 TIKHUB_API_KEY / TIKHUB_API_BASE（经 app.core.config.settings）。
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

# 让脚本能 import 到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings  # noqa: E402

# 端点链涉及的 TikHub 抖音端点：name -> (path, 入参类型)
#   入参类型 aweme_id：用展开后的 aweme_id 作 query
#   入参类型 url     ：用原始分享链接作 query（hybrid 自带短链展开）
ENDPOINTS = {
    "web_v1": ("/api/v1/douyin/web/fetch_one_video", "aweme_id"),
    "web_v2": ("/api/v1/douyin/web/fetch_one_video_v2", "aweme_id"),
    "app_v3": ("/api/v1/douyin/app/v3/fetch_one_video_v3", "aweme_id"),
    "hybrid": ("/api/v1/hybrid/video_data", "url"),
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


async def expand_short_url(url: str) -> str:
    """跟随重定向把短链展开成最终长链。"""
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers={"User-Agent": UA}
    ) as client:
        resp = await client.get(url)
        return str(resp.url)


def extract_aweme_id(long_url: str) -> str | None:
    """从抖音长链里抠出 aweme_id。"""
    m = re.search(r"/video/(\d+)", long_url)
    if m:
        return m.group(1)
    # note/modal 等形式兜底
    m = re.search(r"modal_id=(\d+)", long_url)
    return m.group(1) if m else None


def summarize(name: str, status: int, body: dict | None) -> str:
    """对一份响应做一行体检：是否有 aweme_detail / 无水印直链 / filter reason。"""
    if body is None:
        return f"{name:8} HTTP {status}  <非 JSON 响应>"
    data = body.get("data") if isinstance(body, dict) else None
    detail = None
    if isinstance(data, dict):
        detail = data.get("aweme_detail") or (data if "aweme_id" in data else None)
    has_detail = detail is not None
    video_url = ""
    if isinstance(detail, dict):
        try:
            video_url = detail["video"]["bit_rate"][0]["play_addr"]["url_list"][0]
        except (KeyError, IndexError, TypeError):
            video_url = ""
    reason = ""
    if isinstance(data, dict):
        fl = data.get("filter_list") or []
        if fl and isinstance(fl, list) and isinstance(fl[0], dict):
            reason = f"reason={fl[0].get('reason')}"
    flag = "OK " if video_url else ("DETAIL" if has_detail else "EMPTY")
    return (
        f"{name:8} HTTP {status}  {flag:7} "
        f"aweme_detail={has_detail} url={'有' if video_url else '无'} {reason}"
    )


async def fetch(name: str, path: str, param_kind: str, aweme_id: str | None, share_url: str):
    """打单个端点，返回 (status, body_or_none, raw_text)。不抛错，错误体也要留。"""
    url = f"{settings.TIKHUB_API_BASE}{path}"
    if param_kind == "aweme_id":
        if not aweme_id:
            return None, None, "<跳过：无 aweme_id>"
        params = {"aweme_id": aweme_id}
    else:
        params = {"url": share_url}
    headers = {"Authorization": f"Bearer {settings.TIKHUB_API_KEY}"}
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": UA}) as client:
        resp = await client.get(url, params=params, headers=headers)  # 不 raise
        try:
            body = resp.json()
        except Exception:
            body = None
        return resp.status_code, body, resp.text


async def main():
    ap = argparse.ArgumentParser(description="采集抖音 TikHub 响应作测试 fixtures")
    ap.add_argument("--url", help="抖音分享链接 / 短链 / 长链")
    ap.add_argument("--aweme-id", help="直接指定 aweme_id（与 --url 二选一即可）")
    ap.add_argument("--prefix", default="", help="输出文件名前缀，如 private_ / deleted_")
    ap.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "douyin"),
        help="fixture 输出目录",
    )
    ap.add_argument(
        "--only", nargs="*", choices=list(ENDPOINTS), help="仅采集指定端点（默认全部）"
    )
    args = ap.parse_args()

    if not settings.TIKHUB_API_KEY:
        sys.exit("ERROR: TIKHUB_API_KEY 未配置（检查 .env）")
    if not args.url and not args.aweme_id:
        sys.exit("ERROR: 至少提供 --url 或 --aweme-id 之一")

    share_url = args.url or ""
    aweme_id = args.aweme_id

    if share_url and not aweme_id:
        long_url = await expand_short_url(share_url)
        aweme_id = extract_aweme_id(long_url)
        print(f"短链展开: {share_url}\n     -> {long_url}\n     aweme_id = {aweme_id}\n")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = args.only or list(ENDPOINTS)
    print("=" * 72)
    for name in targets:
        path, kind = ENDPOINTS[name]
        status, body, raw = await fetch(name, path, kind, aweme_id, share_url)
        if status is None:
            print(summarize(name, 0, None))
            continue
        fname = out_dir / f"{args.prefix}{name}.json"
        # 原样保存（抖音视频数据为公开内容；play_addr 为带签名的 CDN 直链，会过期，仅作结构样本）
        if body is not None:
            fname.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            fname.write_text(raw, encoding="utf-8")
        print(summarize(name, status, body), f"-> {fname.name}")
    print("=" * 72)
    print(f"fixtures 写入: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
