#!/usr/bin/env python3
"""
Instagram TikHub 真实响应采集脚本（测试 fixtures）

旧端点 /api/v1/instagram/web_app/fetch_post_media_by_url 已被 TikHub 下线（404），
本脚本抓取当前可用的 v2 / v3 端点响应，供修复与回归测试使用。

用法：
    .venv/bin/python scripts/collect_instagram_fixtures.py --code DR9FE94jBop
    .venv/bin/python scripts/collect_instagram_fixtures.py --url "https://www.instagram.com/reel/DR9FE94jBop"
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def endpoints(code: str, url: str):
    base = settings.TIKHUB_API_BASE
    return {
        "v2_post_info": (f"{base}/api/v1/instagram/v2/fetch_post_info", {"code_or_url": code or url}),
        "v3_by_code": (f"{base}/api/v1/instagram/v3/get_post_info_by_code", {"code": code}),
        "v3_get_post_info": (f"{base}/api/v1/instagram/v3/get_post_info", {"url": url}),
    }


def video_url_in(body) -> str:
    s = json.dumps(body)
    for key in ("video_url", "video_versions", ".mp4"):
        if key in s:
            return key
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="")
    ap.add_argument("--url", default="")
    ap.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "instagram"),
    )
    args = ap.parse_args()
    if not args.code and not args.url:
        sys.exit("ERROR: 需要 --code 或 --url")
    if not settings.TIKHUB_API_KEY:
        sys.exit("ERROR: TIKHUB_API_KEY 未配置")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {settings.TIKHUB_API_KEY}", "User-Agent": UA}

    print("=" * 72)
    for name, (url, params) in endpoints(args.code, args.url).items():
        try:
            with httpx.Client(timeout=60, headers=headers) as c:
                r = c.get(url, params=params)
            try:
                body = r.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                (out / f"{name}.json").write_text(
                    json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            print(f"{name:18} HTTP {r.status_code}  video_hint={video_url_in(body) or '无':10} -> {name}.json")
        except Exception as e:
            print(f"{name:18} EXC {type(e).__name__}: {e}")
    print("=" * 72)
    print(f"fixtures 写入: {out}")


if __name__ == "__main__":
    main()
