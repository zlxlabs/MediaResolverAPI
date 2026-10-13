# X Space 回放下载探针脚本

Task-Id: x-space-replay-probe-20261014-01 · dispatch `dlg-20261013-172131-68bd96`

四个判定点 D1–D4 的一次性实测脚本。运行环境：ffmpeg 6.1.1（系统）、yt-dlp 2026.08.19
（`uv tool run yt-dlp` 临时环境，未入仓依赖）。样本产物全部落在 `/tmp/space-probe/`，
不入库。事实结论见同目录 `probe-report.md`。

## 准备

```sh
mkdir -p /tmp/space-probe && cd /tmp/space-probe
ffmpeg -version | head -1          # 6.1.1-3ubuntu5+esm13
uv tool run yt-dlp --version       # 2026.08.19
SPACE_URL="https://x.com/i/spaces/1AKEmvYqbrOKL"
```

## D1 提取（无 cookies，guest token 路径）

```sh
uv tool run yt-dlp --simulate --dump-single-json "$SPACE_URL" > meta.json
# exit=0，stderr 空，meta.json 5009 字节
python3 -c "import json;m=json.load(open('meta.json'));print(m['url'])"
```

## D2 playlist URL 寿命采样（后台运行 + 结果文件轮询）

单条命令超时上限 25 分钟 < 30 分钟采样跨度，故 nohup 后台跑；完成判据 =
结果文件含 3 行采样记录（外加一行 DONE 标记）。每行记录两个状态码：
`bare`（卡面 Evidence-Commands 原样：`curl -r 0-0` 不带 header）与
`with_headers`（带 yt-dlp `http_headers` 的 UA + `Referer: https://twitter.com/`，
对齐服务端真实调用形态）。

```sh
cat > probe-d2.sh <<'EOF'
#!/bin/sh
URL="$1"; OUT="$2"; START=$(date +%s)
probe() {
  bare=$(curl -s -r 0-0 -o /dev/null -w '%{http_code}' "$URL")
  withhdr=$(curl -s -r 0-0 -o /dev/null -w '%{http_code}' \
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36' \
    -H 'Referer: https://twitter.com/' "$URL")
  now=$(date +%s)
  echo "$(date -Iseconds) elapsed_s=$((now-START)) bare=$bare with_headers=$withhdr" >> "$OUT"
}
probe; sleep 600; probe; sleep 1200; probe
echo "$(date -Iseconds) DONE" >> "$OUT"
EOF
URL=$(python3 -c "import json;print(json.load(open('meta.json'))['url'])")
nohup sh probe-d2.sh "$URL" /tmp/space-probe/d2-samples.txt > d2-nohup.log 2>&1 &
# 主流程推进 D3；期间轮询: wc -l d2-samples.txt >= 3 即完成
```

## D3 完整下载 + remux（AAC 不重编码）

刻意**不加任何 ffmpeg reconnect/重试参数**——本判定点测的就是数小时下载过程中
segment 是否中途过期断流，加重试会掩盖事实。

```sh
URL=$(python3 -c "import json;print(json.load(open('meta.json'))['url'])")
/usr/bin/time -v ffmpeg -y \
  -user_agent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36' \
  -headers $'Referer: https://twitter.com/\r\n' \
  -i "$URL" -c copy -bsf:a aac_adtstoasc /tmp/space-probe/sample.m4a \
  > d3-time.log 2> d3-ffmpeg.log
# 断流核查：ffmpeg 日志中 grep "HTTP error|Server returned|Failed|Error"，命中即断流
ffprobe -v error -show_entries format=duration,size,bit_rate:stream=codec_name,sample_rate,channels,bit_rate -of json sample.m4a
sha256sum sample.m4a
```

## D4 元数据清单

```sh
python3 - <<'EOF'
import json
m = json.load(open('meta.json'))
for k in sorted(m.keys()):
    print(k, '=', json.dumps(m[k], ensure_ascii=False)[:100])
EOF
```

VideoInfo 契约关注字段的存在性与值形态见 `probe-report.md` D4 节。
