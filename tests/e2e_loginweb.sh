#!/usr/bin/env bash
# login-web 端到端演练（假 sau + 真 HTTP 服务 + 真二维码 PNG）。
#
# 为什么需要它：单测只到「函数返回了什么」，而 login-web 的坑全在
# **一个真跑起来的进程**和**真的 HTTP 请求**之间：
#   · 页面是不是真的零外部请求（引了 CDN 就等于把功能绑在公网上）
#   · 状态机 idle → starting → waiting 是不是真的能走过去
#   · 二维码是不是真的能从浏览器取到（不是只有一个路径字符串）
#   · 最关键：**会不会把别的平台的二维码递给你**
#     （sau.newest_qr() 有兜底 glob，网页里用它会让人扫到上一个平台的码，
#       页面看起来完全正常，扫了就是登不上）
#
# 跑法：bash tests/e2e_loginweb.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$(mktemp -d /tmp/vpp-web.XXXXXX)"
PY="${PY:-python3}"
PORT=$(( 18000 + ($$ % 900) ))
FAILED=0

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAILED=1; }
dump() { printf '%s' "$1" | head -c 400 | sed 's/^/    /'; echo; }

SERVER_PID=""
cleanup() {
  # 服务是后台作业，直接 kill 它；但它拉起的假 sau 得另外收
  # （Python 被 SIGTERM 打死时不会走 finally，子进程会变孤儿）。
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  sleep 0.3
  [ -n "$SERVER_PID" ] && kill -9 "$SERVER_PID" 2>/dev/null
  pkill -f "$BASE/sau/sau" 2>/dev/null
  pkill -f "$BASE/sau/python" 2>/dev/null
  rm -rf "$BASE"
}
trap cleanup EXIT

# ── 搭一个假的 sau 环境 ─────────────────────────────────────────
mkdir -p "$BASE/sau/cookies"
export FAKE_CALL_LOG="$BASE/calls.log"
: > "$FAKE_CALL_LOG"

# 假 sau：收到 login 就**自己生成一张二维码 PNG**，路径和命名完全照抄
# sau 的真实规则（cookies/{platform}_{account}_login_qrcode_{时间戳}.png）。
# 这样被验证的就是我们自己的查找逻辑，而不是我编的路径。
cat > "$BASE/sau/sau" <<'STUB'
#!/bin/sh
echo "$*" >> "$FAKE_CALL_LOG"
plat="$1"; shift
case "$1" in
  login)
    acct="我的抖音"
    while [ $# -gt 0 ]; do
      [ "$1" = "--account" ] && { acct="$2"; shift; }
      shift
    done
    # 用标记文件而不是环境变量控制「这次不出码」：服务进程的环境在
    # 它启动那一刻就固定了，之后再 export 是传不进去的。
    if [ ! -f "$FAKE_COOKIES/.noqr" ]; then
      # 各平台的二维码文件名**后缀不一样**（实测抄下来的），
      # 假 sau 必须照着来 —— 不然演练就测不到「认不出小红书/快手的码」这个坑。
      case "$plat" in
        xiaohongshu) sfx="xhs_login_qrcode"; stamp="_$(date +%Y%m%d_%H%M%S)";;
        kuaishou)    sfx="ks_login_qrcode";  stamp="_$(date +%Y%m%d_%H%M%S)";;
        hupu)        sfx="qq_qrcode";        stamp="";;   # 虎扑连时间戳都没有
        *)           sfx="login_qrcode";     stamp="_$(date +%Y%m%d_%H%M%S)";;
      esac
      "$FAKE_PY" "$FAKE_PNG" "$plat" "$acct" \
        "$FAKE_COOKIES/${plat}_${acct}_${sfx}${stamp}.png"
    fi
    # 真 sau 会把二维码用方块字符打到终端。我们的日志里必须把它清掉，
    # 否则日志区被几千个方块刷满，真出错了反而看不见。
    printf '\033[36m🖼️ 二维码已经准备好啦，已保存到: %s\033[0m\n' "$FAKE_COOKIES/x.png"
    printf '████████████████████████████████\n'
    printf '████████████████████████████████\n'
    printf '🧍 请扫码，小人正在耐心等待登录完成\n'
    sleep 20
    ;;
esac
exit 0
STUB
chmod +x "$BASE/sau/sau"

# 假「sau 的 python」：TikTok 这类平台**不经过 sau CLI**，而是由 vp-publish
# 自带的驱动脚本干，驱动脚本又必须用 sau venv 里的解释器跑（playwright 在里头）。
# 这里造一个假解释器，把「网页点一下 → 真的把驱动脚本拉起来 → 真的出码」
# 这条链演练到。不这么做的话，这段分发逻辑只能靠读源码「确认」。
cat > "$BASE/sau/python" <<'STUB'
#!/bin/sh
echo "PYTHON $*" >> "$FAKE_CALL_LOG"
driver="$1"; shift
[ "$1" = "login" ] || exit 0
qr_dir=""; prefix=""
while [ $# -gt 0 ]; do
  case "$1" in
    --qr-dir)    qr_dir="$2"; shift;;
    --qr-prefix) prefix="$2"; shift;;
  esac
  shift
done
"$FAKE_PY" "$FAKE_PNG" tiktok "${prefix#tiktok_}" \
  "$qr_dir/${prefix}_tk_login_qrcode_$(date +%Y%m%d_%H%M%S).png"
printf '[二维码] %s\n' "$qr_dir/${prefix}_tk_login_qrcode_x.png"
sleep 20
STUB
chmod +x "$BASE/sau/python"

# 造一张**合法**的 PNG，并把「谁/哪个账号」写进 tEXt 块 ——
# 这样后面可以直接 grep 二进制，确认拿到的是哪张码。
cat > "$BASE/mkpng.py" <<'PYEOF'
import struct, sys, zlib
plat, acct, out = sys.argv[1], sys.argv[2], sys.argv[3]
def chunk(tag, data):
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)
w = h = 8
raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
       + chunk(b"tEXt", b"Mark\x00" + f"{plat}/{acct}".encode())
       + chunk(b"IDAT", zlib.compress(raw))
       + chunk(b"IEND", b""))
open(out, "wb").write(png)
PYEOF

export FAKE_PY="$PY" FAKE_PNG="$BASE/mkpng.py" FAKE_COOKIES="$BASE/sau/cookies"

# 已登录的平台（用来验证页面上的「已登录」徽章）
echo '[]' > "$BASE/sau/cookies/bilibili_我的B站.json"

cat > "$BASE/config.json" <<EOF
{
  "sau": {
    "root": "$BASE/sau",
    "bin": "$BASE/sau/sau",
    "python": "$BASE/sau/python"
  },
  "cover": false,
  "watch_dirs": ["$BASE/videos"]
}
EOF
export VPP_CONFIG="$BASE/config.json"
export XDG_STATE_HOME="$BASE/state"

W() { ( cd "$REPO" && "$PY" ./vp-publish "$@" ); }

# 起服务。**不用子 shell**：`( ... ) &` 的 $! 是子 shell 的 pid，
# kill 它杀不掉里面的 python，端口会一直被占着。
# 直接跑脚本路径即可 —— Python 会把脚本所在目录加进 sys.path，与 cwd 无关。
start_server() {                       # start_server <日志文件> <额外参数...>
  local log="$1"; shift
  "$PY" "$REPO/vp-publish" login-web --host 127.0.0.1 --port "$PORT" "$@" \
    > "$log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then return 0; fi
    sleep 0.25
  done
  return 1
}

# ── 起服务 ─────────────────────────────────────────────────────
note "0. 起 login-web（后台，端口 $PORT）"
start_server "$BASE/server.log" --no-token \
  || { bad "服务没起来"; dump "$(cat "$BASE/server.log")"; exit 1; }
ok "服务起来了（pid $SERVER_PID）"

U="http://127.0.0.1:$PORT"

# ── 1. 页面 ─────────────────────────────────────────────────────
note "1. 页面本身：能打开，且**零外部请求**"
HTML="$(curl -fsS "$U/")"
if echo "$HTML" | grep -q "扫码登录"; then ok "页面有标题"; else bad "页面不对劲"; fi

# 这条是硬要求：板子可能连不上外网，引了 CDN 就等于整页失灵。
EXT=""
for pat in 'http://' 'https://' '//cdn' 'cdn\.' 'layui' 'unpkg' 'jsdelivr' 'googleapis'; do
  if echo "$HTML" | grep -qE "$pat"; then EXT="$EXT $pat"; fi
done
if [ -z "$EXT" ]; then ok "没有任何外部请求（CDN/字体/框架都没引）"
else bad "页面里有外部引用：$EXT"; fi

if echo "$HTML" | grep -q 'id="qr"'; then ok "有二维码容器"; else bad "找不到二维码容器"; fi
# 过期码要有地方说清楚 —— 实测踩到过：页面打开时码已经过期 114 秒，
# 原来会当成正常码显示出来，用户扫了就是「该二维码已过期」。
if echo "$HTML" | grep -q 'id="qstale"'; then ok "有「码已过期」的提示位"; else bad "缺过期提示位"; fi
if echo "$HTML" | grep -q 'qr_age'; then ok "过期判断用的是服务端算好的 qr_age"; else bad "没有 qr_age"; fi

# ── 2. 初始状态 ─────────────────────────────────────────────────
note "2. 初始状态是 idle，且认得所有平台"
ST="$(curl -fsS "$U/api/state")"
echo "$ST" | grep -q '"status": *"idle"' && ok "status=idle" || { bad "初始不是 idle"; dump "$ST"; }
echo "$ST" | grep -q '"douyin"' && ok "平台列表里有抖音" || bad "平台列表缺抖音"
echo "$ST" | grep -q '"refresh_after"' && ok "带上了自动换码间隔" || bad "缺 refresh_after"
echo "$ST" | grep -q '"我的B站"' && ok "认得出已登录的账号（页面要显示徽章）" || bad "没发现已登录账号"

# ── 3. 选平台 → 出码 ────────────────────────────────────────────
note "3. 点抖音 → 起 sau → 出二维码"
CODE="$(curl -sS -o "$BASE/start.json" -w '%{http_code}' -X POST --data 'douyin' "$U/api/start")"
if [ "$CODE" = 200 ]; then ok "POST /api/start 返回 200"; else bad "start 返回 $CODE"; fi
dump "$(cat "$BASE/start.json")"
grep -q '"ok": *true' "$BASE/start.json" && ok "接受了这个平台" || bad "没接受"
grep -q 'douyin login' "$FAKE_CALL_LOG" && ok "真的把 sau 拉起来了" \
  || bad "没调 sau：$(cat "$FAKE_CALL_LOG")"

# 等到出码
GOT=0
for _ in $(seq 1 60); do
  ST="$(curl -fsS "$U/api/state")"
  echo "$ST" | grep -q '"status": *"waiting"' && { GOT=1; break; }
  sleep 0.25
done
if [ "$GOT" = 1 ]; then ok "状态走到了 waiting"; else bad "一直没出码"; dump "$ST"; fi
echo "$ST" | grep -q '"qr_mtime": *[1-9]' && ok "带上了二维码时间戳" || bad "qr_mtime 是空的"

# ── 4. 二维码真的取得到 ─────────────────────────────────────────
note "4. 浏览器拿得到二维码（不是只有一个路径字符串）"
curl -fsS "$U/api/qr.png" -o "$BASE/got.png"
MAGIC="$(head -c 4 "$BASE/got.png" | od -An -tx1 | tr -d ' \n')"
if [ "$MAGIC" = "89504e47" ]; then ok "是合法 PNG"
else bad "拿到的不是 PNG（magic=$MAGIC）"; fi
if grep -aq 'douyin/' "$BASE/got.png"; then ok "确实是抖音的码"
else bad "拿到的码不是抖音的"; fi

# ── 5. 只认本平台的码（最要紧的一条）────────────────────────────
note "5. 别的平台的码更新，也不能递给我"
# 造一张**更新**的 B 站二维码。sau.newest_qr() 的兜底 glob 会捞到它，
# 页面看起来正常，用户扫了却登不上 —— 这就是要防的事。
"$PY" "$BASE/mkpng.py" bilibili 我的B站 \
  "$BASE/sau/cookies/bilibili_我的B站_login_qrcode_20991231_235959.png"
sleep 1.1
curl -fsS "$U/api/qr.png" -o "$BASE/got2.png"
if grep -aq 'douyin/' "$BASE/got2.png"; then ok "仍然给的是抖音的码（没被更新的 B 站码顶掉）"
else bad "把别的平台的码递出来了！"; fi

# ── 5b. 同一个平台换一张：旧码要留着 ────────────────────────────
note "5b. 换一张码时，旧码继续挂着（chromium 起来要十几秒）"
curl -sS -X POST --data 'douyin' "$U/api/start" -o "$BASE/re.json"
if grep -q '"reused": *true' "$BASE/re.json"; then ok "认得出这是「换一张」"
else bad "没识别成换码：$(cat "$BASE/re.json")"; fi
CODE="$(curl -sS -o "$BASE/got3.png" -w '%{http_code}' "$U/api/qr.png")"
if [ "$CODE" = 200 ] && grep -aq 'douyin/' "$BASE/got3.png"; then
  ok "换码期间旧码还在（页面不会空十几秒）"
else bad "换码期间旧码被清掉了（HTTP $CODE）"; fi

# ── 5c. 换平台：绝不能留上一张 ──────────────────────────────────
note "5c. 换平台时，上一张码必须清掉"
rm -f "$BASE/sau/cookies/xiaohongshu_我的小红书_xhs_login_qrcode_"*.png
: > "$BASE/sau/cookies/.noqr"          # 让假 sau 这次不出码
curl -sS -X POST --data 'xiaohongshu' "$U/api/start" -o "$BASE/re2.json"
if grep -q '"reused": *false' "$BASE/re2.json"; then ok "认得出这是换平台"
else bad "把换平台当成换码了：$(cat "$BASE/re2.json")"; fi
CODE="$(curl -sS -o "$BASE/got4.png" -w '%{http_code}' "$U/api/qr.png")"
if [ "$CODE" = 404 ]; then ok "抖音的码没被留下来给小红书用"
else bad "把抖音的码递给了小红书（HTTP $CODE）—— 扫了就是登不上！"; fi
ST="$(curl -fsS "$U/api/state")"
if echo "$ST" | grep -q '"qr_mtime": *0'; then ok "本平台没码时就说没码，不拿别人的凑"
else bad "没码却报有码：$(echo "$ST" | head -c 200)"; fi

# 再补一刀：造一张**刚生成的**抖音码（比这次 spawn 还新）。
# 它在时间上完全"够格"，唯一的问题是**不是这个平台的**。
"$PY" "$BASE/mkpng.py" douyin 我的抖音 \
  "$BASE/sau/cookies/douyin_我的抖音_login_qrcode_$(date +%Y%m%d_%H%M%S).png"
sleep 0.5
CODE="$(curl -sS -o "$BASE/got5.png" -w '%{http_code}' "$U/api/qr.png")"
if [ "$CODE" = 404 ]; then ok "刚生成的抖音码也没被小红书捡走"
else bad "小红书捡了抖音的码（HTTP $CODE）"; fi
rm -f "$BASE/sau/cookies/.noqr"

# ── 5d. 各平台二维码文件名后缀不一样，都得认得出来 ──────────────
note "5d. 小红书/快手/虎扑的码文件名后缀跟抖音不一样，也要认得出来"
# 实测抄下来的真实文件名：xiaohongshu_..._xhs_login_qrcode_...png、
# kuaishou_..._ks_login_qrcode_...png、hupu_..._qq_qrcode.png（连时间戳都没有）。
# 原来按 {platform}_{account}_login_qrcode_*.png 硬编 → 这三个平台
# **静默失效**（网页上显示「没有二维码」，其实码就在那儿）。假 sau 现在
# 按各平台真实后缀出码，所以这一段能真的守住它。
for spec in "xiaohongshu:我的小红书:xhs_login_qrcode" \
            "kuaishou:我的快手:ks_login_qrcode" \
            "hupu:我的虎扑:qq_qrcode"; do
  PLAT="${spec%%:*}"; REST="${spec#*:}"; ACCT="${REST%%:*}"
  curl -sS -X POST --data "$PLAT" "$U/api/start" >/dev/null
  FOUND=0
  for _ in $(seq 1 40); do
    sleep 0.25
    ST="$(curl -fsS "$U/api/state")"
    echo "$ST" | grep -q '"qr_mtime": *[1-9]' && { FOUND=1; break; }
  done
  if [ "$FOUND" = 1 ] && [ "$(curl -sS -o "$BASE/got6.png" -w '%{http_code}' "$U/api/qr.png")" = 200 ]; then
    ok "$PLAT 的码认得出来（$ACCT）"
  else
    bad "$PLAT 的码认不出来 —— 页面会显示「没有二维码」"
  fi
done

# ── 5e. TikTok：sau CLI 里没有它，得走 vp-publish 自带的驱动 ────
note "5e. TikTok 不走 sau CLI，要走自带驱动（用 sau 的 python 跑 tk_driver.py）"
# 为什么单独验这个：TikTok 是唯一一个 `driver != "cli"` 的平台。
# 它走的是完全另一条进程链（sau 的 python + 包内的驱动脚本），
# 任何一环接错都表现为「点了没反应」或「页面一直转圈」——
# 而这正是本模块开头警告的那类坑。单测只验 argv 形状，验不到这里。
curl -sS -X POST --data 'tiktok' "$U/api/start" -o "$BASE/tk.json"
FOUND=0
for _ in $(seq 1 40); do
  sleep 0.25
  ST="$(curl -fsS "$U/api/state")"
  echo "$ST" | grep -q '"qr_mtime": *[1-9]' && { FOUND=1; break; }
done
if [ "$FOUND" = 1 ]; then ok "TikTok 的码出来了"
else bad "TikTok 起不来"; dump "$(cat "$BASE/tk.json")"; fi

if grep -q 'tk_driver.py login' "$FAKE_CALL_LOG"; then
  ok "真的把驱动脚本拉起来了（不是去调 sau tiktok）"
else bad "没拉起驱动脚本"; dump "$(tail -3 "$FAKE_CALL_LOG")"; fi
if grep -q -- '--qr-prefix tiktok_' "$FAKE_CALL_LOG"; then
  ok "二维码前缀带平台名（前缀不对就会捞到别的平台的码）"
else bad "前缀里没有平台名"; dump "$(tail -3 "$FAKE_CALL_LOG")"; fi

if [ "$(curl -sS -o "$BASE/tk.png" -w '%{http_code}' "$U/api/qr.png")" = 200 ] \
   && grep -aq 'tiktok/' "$BASE/tk.png"; then
  ok "网页上拿到的确实是 TikTok 自己的码"
else bad "拿到的不是 TikTok 的码"; fi

# ── 6. 日志里的方块要被清掉 ─────────────────────────────────────
note "6. 日志区不该被终端二维码刷满"
curl -fsS -X POST --data 'douyin' "$U/api/start" >/dev/null
sleep 0.8
ST="$(curl -fsS "$U/api/state")"
if echo "$ST" | grep -q '二维码已经准备好啦'; then ok "看得到真日志"; else bad "日志没读到"; fi
if echo "$ST" | grep -q '█'; then bad "方块字符漏进日志了"; else ok "方块字符被清掉了"; fi

# ── 7. 停止 ─────────────────────────────────────────────────────
note "7. 停止后回到 idle，且不留后台进程"
curl -fsS -X POST "$U/api/stop" >/dev/null
sleep 0.4
ST="$(curl -fsS "$U/api/state")"
echo "$ST" | grep -q '"status": *"idle"' && ok "回到 idle" || { bad "没回到 idle"; dump "$ST"; }

# ── 8. 认不出的平台要说人话 ─────────────────────────────────────
note "8. 传个不存在的平台，得给出能看懂的理由"
curl -sS -o "$BASE/bad.json" -X POST --data 'meituan' "$U/api/start"
if grep -q '认不出' "$BASE/bad.json"; then ok "说的是「认不出平台」"
else bad "报错驴唇不对马嘴"; dump "$(cat "$BASE/bad.json")"; fi

# ── 9. token ────────────────────────────────────────────────────
note "9. 带 token 时，没令牌的请求要被挡掉"
kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null
start_server "$BASE/server2.log" || { bad "第二个服务没起来"; dump "$(cat "$BASE/server2.log")"; }
# 从启动日志里把带 token 的地址捞出来
TOK="$(grep -o 'k=[A-Za-z0-9_-]*' "$BASE/server2.log" | head -1 | cut -d= -f2)"
if [ -n "$TOK" ]; then ok "启动时打印了带 token 的地址"; else bad "没打印 token 地址"; dump "$(cat "$BASE/server2.log")"; fi

C1="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/state")"
[ "$C1" = 401 ] && ok "没 token → 401" || bad "没 token 却给了 $C1"
C2="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/state?k=$TOK")"
[ "$C2" = 200 ] && ok "带 token → 200" || bad "带 token 却是 $C2"
C3="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")"
[ "$C3" = 200 ] && ok "首页免 token（否则没法把链接发给自己）" || bad "首页被挡了：$C3"

# ── 收尾 ────────────────────────────────────────────────────────
printf '\n'
if [ "$FAILED" = 0 ]; then
  printf '\033[1;32mlogin-web 演练全部通过\033[0m\n'
else
  printf '\033[1;31mlogin-web 演练有失败\033[0m\n'
fi
exit "$FAILED"
