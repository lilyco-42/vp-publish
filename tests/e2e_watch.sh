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

# ── 8. 服务先上线、后登录（最实际的使用顺序）─────────────────────
#
# 真实流程是：先把 systemd 服务装好跑起来，过几天才想起来扫码登录。
# 所以必须做到三件事：
#   · 没有平台时**不退出**（退出 + Restart=always = 重启死循环）
#   · 也不登记任何东西（baseline_done 不能翻，否则积压的老视频会失去保护）
#   · 登录后自动开始工作，**不用重启服务**
note "8. 服务先上线、后登录"
B2="$BASE/b2"
mkdir -p "$B2/sau/cookies" "$B2/videos" "$B2/state"
cp "$BASE/sau/sau" "$B2/sau/sau"; chmod +x "$B2/sau/sau"
head -c 300000 /dev/urandom > "$B2/videos/2026-09-01-积压老片.mp4"
cat > "$B2/config.json" <<EOF
{ "sau": { "root": "$B2/sau", "bin": "$B2/sau/sau" },
  "cover": false, "watch_dirs": ["$B2/videos"] }
EOF
W2STATE="$B2/state/vp-publish/watch.json"

run2() { ( cd "$REPO" && VPP_CONFIG="$B2/config.json" XDG_STATE_HOME="$B2/state" \
           "$PY" ./vp-publish watch "$@" ); }
: > "$FAKE_CALL_LOG"

OUT="$(run2 --once 2>&1)"; rc=$?
dump "$OUT"
if [ "$rc" = "2" ]; then ok "没有平台时退出码是 2（可脚本化判断）"
else bad "退出码应为 2，实际 $rc"; fi
if echo "$OUT" | grep -q "还没有已登录的平台"; then ok "说清了为什么没动静"
else bad "提示不清楚"; fi
expect_quiet "没有平台时不乱发"

if "$PY" - "$W2STATE" <<'PYCHK'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit("状态文件读不出来")
bad = []
if d.get("published"):
    bad.append("published 非空")
if d.get("baseline_done"):
    bad.append("baseline_done 已翻（积压老片会失去保护）")
sys.exit("；".join(bad) if bad else 0)
PYCHK
then ok "状态干净：没登记、baseline 未翻（积压老片仍受保护）"
else bad "状态被污染了"; fi

# 「扫码登录」
echo '[]' > "$B2/sau/cookies/douyin_测试号.json"
OUT="$(run2 --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "目标平台（1）"; then ok "登录被自动发现（没重启服务）"
else bad "没发现新登录的平台"; fi
expect_quiet "登录后的第一次扫描只登记，不发"

OUT="$(run2 --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "跳过库存"; then ok "积压老片被正确保护"
else bad "积压老片没被保护（登录后就把它发出去了）"; fi
expect_quiet "积压老片没被发出去"

head -c 200000 /dev/urandom > "$B2/videos/2026-09-27-登录后新片.mp4"
run2 --once >/dev/null 2>&1
OUT="$(run2 --once 2>&1)"; dump "$OUT"
if echo "$OUT" | grep -q "已发布"; then ok "登录之后的新视频正常发出"
else bad "新视频没发出去"; fi
expect_calls 1 "只发了新片（老片没被连带发出）"

echo
if [ "$FAILED" = 0 ]; then
  printf '\033[1;32m全部通过\033[0m\n'
else
  printf '\033[1;31m有失败项\033[0m\n'
fi
exit "$FAILED"
