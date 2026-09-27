#!/usr/bin/env bash
# watch 模式端到端演练（用假 sau，不碰真环境、不发任何东西）。
#
# 为什么需要它：watch 的坑全在**跨轮**的行为上（库存保护、等文件写完、
# 不重复发），单测覆盖不到「第 N 轮和第 N+1 轮之间的状态」。
# 实测就靠这个脚本抓到过一次真 bug：库存保护写成了全局开关，
# 结果第二轮库存全变成「就绪」时开关已经翻过去了，整批被发出去。
#
# 验证六件事：
#   1. 库存（启动时就存在的视频）跨轮只登记、不发布
#   2. 正在写入的文件不会被发出去；写完稳定后才发
#   3. 已发布的不会重发（幂等）
#   4. 失败会记进状态，且不自动重试
#   5. --dry-run 不写发布记录，试运行完还能真发
#   6. forget 之后能重发
#
# 跑法：bash tests/e2e_watch.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$(mktemp -d /tmp/vpp-e2e.XXXXXX)"
PY="${PY:-python3}"
FAILED=0

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAILED=1; }

cleanup() { rm -rf "$BASE"; }
trap cleanup EXIT

# ── 搭一个假的 sau 环境 ─────────────────────────────────────────
mkdir -p "$BASE/sau/cookies" "$BASE/videos" "$BASE/state"
export FAKE_CALL_LOG="$BASE/calls.log"
: > "$FAKE_CALL_LOG"

# 假 sau：把收到的参数追加到调用日志。
# 注意不能靠它的 stdout 判断 —— vp-publish 用 capture_output 跑上传，
# 子进程的输出是被吞掉的（这是实测踩过的坑），只有日志文件靠得住。
cat > "$BASE/sau/sau" <<'STUB'
#!/bin/sh
echo "$*" >> "$FAKE_CALL_LOG"
echo "FAKE-SAU argv: $*"
exit "${FAKE_SAU_EXIT:-0}"
STUB
chmod +x "$BASE/sau/sau"

# 假账号（sau 的命名规则：{platform}_{account}.json）
echo '[]' > "$BASE/sau/cookies/douyin_测试号.json"

# 库存：一个早就存在的视频
head -c 300000 /dev/urandom > "$BASE/videos/2026-09-01-库存老片.mp4"

cat > "$BASE/config.json" <<EOF
{
  "sau": { "root": "$BASE/sau", "bin": "$BASE/sau/sau" },
  "cover": false,
  "watch_dirs": ["$BASE/videos"]
}
EOF
export VPP_CONFIG="$BASE/config.json"
export XDG_STATE_HOME="$BASE/state"          # watch 状态落在 $BASE/state/vp-publish/

W() { ( cd "$REPO" && "$PY" ./vp-publish watch "$@" ); }

# 调用日志里的行数 = 真正调了 sau 几次
calls() { wc -l < "$FAKE_CALL_LOG" | tr -d ' '; }
expect_calls() {                       # expect_calls <期望次数> <说明>
  local got; got="$(calls)"
  if [ "$got" = "$1" ]; then ok "$2（sau 调用 $got 次）"
  else bad "$2：期望调用 $1 次，实际 $got 次"; fi
}
expect_quiet() {                       # expect_quiet <说明>  —— 一次都不该调
  expect_calls 0 "$1"
}
dump() { echo "$1" | sed 's/^/    /'; }

# ── 1. 库存保护（跨轮）──────────────────────────────────────────
note "1. 库存保护：启动时就存在的视频，跨轮也不发"
OUT="$(W --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "等待"; then ok "第 1 轮：在等它稳定"; else bad "第 1 轮就该等"; fi
expect_quiet "第 1 轮不该调 sau"

OUT="$(W --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "跳过库存"; then ok "第 2 轮：稳定了，但仍被当作库存跳过"
else bad "库存保护失效（第 2 轮把它发出去了？）"; fi
expect_quiet "库存没被发出去"

# ── 2. 等文件写完 ───────────────────────────────────────────────
note "2. 正在写入的文件不发，写完稳定后才发"
head -c 100000 /dev/urandom > "$BASE/videos/2026-09-27-新片.mp4"
OUT="$(W --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "等待：2026-09-27-新片.mp4"; then ok "第 1 轮：在等新片写完"
else bad "第 1 轮就该等新片"; fi
expect_quiet "没发半截文件"

head -c 400000 /dev/urandom >> "$BASE/videos/2026-09-27-新片.mp4"   # 还在长
OUT="$(W --once 2>&1)"
expect_quiet "大小变了，继续等（稳定计数要重置）"

OUT="$(W --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "已发布"; then ok "大小稳定后发出去了"; else bad "该发了却没发"; fi
if grep -q "douyin upload-video" "$FAKE_CALL_LOG"; then ok "调用的是 douyin upload-video"
else bad "调用的命令不对：$(cat "$FAKE_CALL_LOG")"; fi
expect_calls 1 "只发了一次"

# ── 3. 幂等：不重发 ─────────────────────────────────────────────
note "3. 已发布的不重发"
W --once >/dev/null 2>&1
expect_calls 1 "又扫了一轮，没有重发"

# ── 4. 失败不自动重试 ───────────────────────────────────────────
note "4. 失败记进状态，且不自动重试"
head -c 250000 /dev/urandom > "$BASE/videos/2026-09-27-坏片.mp4"
W --once >/dev/null 2>&1                    # 登记大小
OUT="$(FAKE_SAU_EXIT=3 W --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "失败 1"; then ok "记为失败"; else bad "没有记成失败"; fi
expect_calls 2 "坏片试了一次"

OUT="$(FAKE_SAU_EXIT=3 W --once 2>&1)"
if echo "$OUT" | grep -q "失败"; then bad "失败了还重试"; else ok "失败不自动重试"; fi
expect_calls 2 "之后没有再调 sau"

# ── 5. dry-run 不污染状态 ───────────────────────────────────────
note "5. --dry-run 不写发布记录"
head -c 220000 /dev/urandom > "$BASE/videos/2026-09-27-试运行片.mp4"
W --once >/dev/null 2>&1                    # 登记大小
OUT="$(W --once --dry-run 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q '^\s*\$ '; then ok "试运行打印出了要执行的命令"
else bad "试运行没打印命令（等于没试）"; fi
expect_calls 2 "试运行没有真的调用 sau"

OUT="$(W --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "试运行片"; then ok "试运行之后仍然真的发了"
else bad "dry-run 把状态写脏了"; fi
expect_calls 3 "真发了一次"

# ── 6. forget 之后能重发 ────────────────────────────────────────
note "6. forget 之后能重发"
( cd "$REPO" && "$PY" ./vp-publish forget "$BASE/videos/2026-09-27-新片.mp4" ) 2>&1 | sed 's/^/    /'
W --once >/dev/null 2>&1                    # 记录被清了 → 重新登记
OUT="$(W --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "新片"; then ok "forget 之后被重新捡起来了"
else bad "forget 没清干净（watch 记录还在挡着）"; fi
expect_calls 4 "重发了一次"

# ── 7. 自定义状态文件 ───────────────────────────────────────────
note "7. --state 自定义状态文件（且 forget --state 认同一个）"
CUSTOM="$BASE/custom-watch.json"
W --once --state "$CUSTOM" >/dev/null 2>&1
if [ -f "$CUSTOM" ]; then ok "--state 生效（文件已创建）"; else bad "--state 没生效"; fi
( cd "$REPO" && "$PY" ./vp-publish forget --state "$CUSTOM" \
    "$BASE/videos/2026-09-27-新片.mp4" ) 2>&1 | sed 's/^/    /'

echo
if [ "$FAILED" = 0 ]; then
  printf '\033[1;32m全部通过\033[0m\n'
else
  printf '\033[1;31m有失败项\033[0m\n'
fi
exit "$FAILED"
