#!/usr/bin/env python3
"""
微信视频号 TikHub 真实响应采集脚本（用于测试 fixtures）

把一个视频号分享链接 / object_id 打到
POST /api/v1/wechat_channels/v2/fetch_video_detail，脱敏后保存到
tests/fixtures/wechat_channels/。

关键设计：
- 直接用 httpx POST JSON，**不调 raise_for_status**，错误响应体也要留。
- raw 必须传 false（默认 true 会返回另一套嵌套结构）。
- 不展开 sph 短链：TikHub 直接吃 share_url。
- 落盘前把 media.url / url_token / full_url / decode_key / cover_url /
  cover_url_token、request_id、cache_url、debug_info 替换成 REDACTED。

用法：
    .venv/bin/python scripts/collect_wechat_channels_fixtures.py --url "https://weixin.qq.com/sph/AOzokRxWHz"
    .venv/bin/python scripts/collect_wechat_channels_fixtures.py --object-id 14998022876670594427

环境：从 .env 读取 TIKHUB_API_KEY / TIKHUB_API_BASE（经 app.core.config.settings）。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings  # noqa: E402

ENDPOINT_PATH = "/api/v1/wechat_channels/v2/fetch_video_detail"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

REDACT_KEYS = frozenset({
    "decode_key",
    "full_url",
    "url_token",
    "url",
    "cover_url",
    "cover_url_token",
    "request_id",
    "cache_url",
    "debug_info",
})


def redact(obj):
    """递归把凭据类字段替换成 REDACTED。"""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in REDACT_KEYS:
                out[key] = "REDACTED"
            else:
                out[key] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return obj


def summarize(status: int, body: dict | None) -> str:
    if body is None:
        return f"HTTP {status}  <非 JSON 响应>"
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return f"HTTP {status}  EMPTY data={data!r}"
    object_type = data.get("object_type")
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    return (
        f"HTTP {status}  object_type={object_type} id={data.get('id')} "
        f"duration={media.get('duration')} title={(data.get('title') or '')[:40]}"
    )


async def fetch(payload: dict):
    url = f"{settings.TIKHUB_API_BASE}{ENDPOINT_PATH}"
    headers = {"Authorization": f"Bearer {settings.TIKHUB_API_KEY}", "User-Agent": UA}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers=headers)
        try:
            body = resp.json()
        except Exception:
            body = None
        return resp.status_code, body, resp.text


async def main():
    ap = argparse.ArgumentParser(description="采集微信视频号 TikHub 响应作测试 fixtures")
    ap.add_argument("--url", help="视频号分享链接（share_url）")
    ap.add_argument("--object-id", help="直接指定 object_id（与 --url 二选一即可）")
    ap.add_argument("--prefix", default="", help="输出文件名前缀")
    ap.add_argument(
        "--out-dir",
        default=str(
            Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "wechat_channels"
        ),
        help="fixture 输出目录",
    )
    args = ap.parse_args()

    if not settings.TIKHUB_API_KEY:
        sys.exit("ERROR: TIKHUB_API_KEY 未配置（检查 .env）")
    if not args.url and not args.object_id:
        sys.exit("ERROR: 至少提供 --url 或 --object-id 之一")

    payload = {"raw": False}
    if args.object_id:
        payload["object_id"] = args.object_id
    else:
        payload["share_url"] = args.url

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    status, body, raw = await fetch(payload)
    fname = out_dir / f"{args.prefix}detail.json"
    if body is not None:
        fname.write_text(
            json.dumps(redact(body), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        fname.write_text(raw, encoding="utf-8")
    print(summarize(status, body), f"-> {fname.name}")
    print(f"fixtures 写入: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
