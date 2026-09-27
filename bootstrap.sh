#!/usr/bin/env bash
# bootstrap.sh —— 一键把发布环境装好。
#
# 这个脚本里**每一行都在真机上踩过坑**，不是照抄文档写的。清单：
#
#   1. 板子的 apt 列表可能是空的（/var/lib/apt/lists 里 0 个 Packages 文件）
#      → 表现是「已装的包也没有候选版本」，apt-get install 装不了任何东西。
#      → 所以第一步必须 apt-get update，而且不能假设它跑过。
#   2. 板子可能没有 git（radxa 的 Debian 镜像默认不带）。
#   3. 板子可能没有 python3-venv 的 ensurepip
#      → `python3 -m venv` 报 "ensurepip is not available"。
#   4. **sau 的 pyproject 要求 python >=3.10,<3.13，而板子是 3.13.5**
#      → pip 直接拒绝安装。只能 --ignore-requires-python（依赖其实都支持 3.13）。
#   5. **sau 的 pyproject 只列了 patchright，但 9 个文件 import playwright**
#      → 而 sau_cli.py 在导入阶段就 import baijiahao_uploader
#      → 缺 playwright 时**整个 CLI 起不来**（不是只有百家号不能用）。
#   6. sau 的 CLI 需要 conf.py，仓库里只有 conf.example.py。
#   7. PyPI 直连在板子上不通（超时），清华/阿里镜像通 → pip 必须走镜像。
#   8. npmmirror 的 playwright 镜像路径已失效（返回阿里云错误 XML）
#      → 必须用官方 CDN cdn.playwright.dev（实测 2MB/s）。
#   9. patchright 和 playwright 各要一套 chromium，都要装。
#
# 用法：
#   bash bootstrap.sh              # 装到默认位置（~/sau）
#   SAU_ROOT=/opt/sau bash bootstrap.sh
#   bash bootstrap.sh --check      # 只体检，不改任何东西
set -euo pipefail

SAU_ROOT="${SAU_ROOT:-$HOME/sau}"
SAU_REPO="${SAU_REPO:-https://github.com/dreammis/social-auto-upload.git}"
PIP_MIRROR="${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PLAYWRIGHT_HOST="${PLAYWRIGHT_HOST:-}"      # 留空 = 用官方 CDN（实测最快）
SKIP_APT="${SKIP_APT:-0}"
MODE="install"

for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    -h|--help) awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"; exit 0 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" != "0" ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  fi
fi

run_root() {
  if [ -n "$SUDO" ]; then
    $SUDO "$@"
  else
    "$@"
  fi
}

# ── 体检模式 ────────────────────────────────────────────────────
if [ "$MODE" = "check" ]; then
  step "体检（不改任何东西）"
  command -v python3 >/dev/null && ok "python3 $(python3 -V 2>&1 | awk '{print $2}')" || bad "没有 python3"
  python3 -c 'import ensurepip' 2>/dev/null && ok "python3-venv（ensurepip 可用）" || bad "缺 python3-venv"
  command -v git >/dev/null && ok "git $(git --version | awk '{print $3}')" || bad "没有 git"
  [ -d "$SAU_ROOT/.git" ] && ok "sau 已克隆：$SAU_ROOT" || bad "sau 未克隆：$SAU_ROOT"
  [ -x "$SAU_ROOT/.venv/bin/sau" ] && ok "sau 命令可用" || bad "sau 命令不可用"
  [ -f "$SAU_ROOT/conf.py" ] && ok "conf.py 已生成" || bad "缺 conf.py"
  "$SAU_ROOT/.venv/bin/python" -c 'import playwright' 2>/dev/null \
    && ok "playwright 已装" || bad "缺 playwright（sau 的 pyproject 漏了它，必须手动装）"
  "$SAU_ROOT/.venv/bin/python" -c 'import patchright' 2>/dev/null \
    && ok "patchright 已装" || bad "缺 patchright"
  ls "$HOME/.cache/ms-playwright"/chromium-* >/dev/null 2>&1 \
    && ok "chromium 已下载" || bad "chromium 未下载"
  command -v ffmpeg >/dev/null && ok "ffmpeg 可用（能自动生成封面）" || note "没有 ffmpeg（封面功能会跳过，不影响上传）"
  exit 0
fi

# ── 1. 系统依赖 ─────────────────────────────────────────────────
step "1/6 系统依赖"
if [ "$SKIP_APT" = "1" ]; then
  note "SKIP_APT=1，跳过"
else
  # 坑 1：apt 列表可能是空的。apt-get update 不能省。
  # 判据：/var/lib/apt/lists 里一个 Packages 文件都没有。
  # 这时「已装的包也没有候选版本」，apt-get install 装不了任何东西。
  if [ "$(ls /var/lib/apt/lists/*Packages* 2>/dev/null | wc -l)" -eq 0 ]; then
    note "apt 列表是空的（已装的包都没有候选版本）—— 先 apt-get update"
    run_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq || {
      bad "apt-get update 失败。检查网络和 /etc/apt/sources.list.d/"
      exit 1
    }
  fi

  PKGS=""
  # 坑 2：git
  command -v git >/dev/null 2>&1 || PKGS="$PKGS git"
  # 坑 3：venv 的 ensurepip
  python3 -c 'import ensurepip' 2>/dev/null || PKGS="$PKGS python3-venv"
  # ffmpeg 用于自动生成封面（非必需，但值得装）
  command -v ffmpeg >/dev/null 2>&1 || PKGS="$PKGS ffmpeg"
  # chromium 的运行库 —— 板子自带 chromium-browser 但常常缺 libnss3
  PKGS="$PKGS libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
libdrm2 libgbm1 libasound2t64 libxkbcommon0 libxcomposite1 libxdamage1 \
libxfixes3 libxrandr2 libpango-1.0-0 libcairo2 fonts-noto-cjk"

  if [ -n "$(echo $PKGS | tr -d ' ')" ]; then
    note "安装：$(echo $PKGS | tr '\n' ' ')"
    # shellcheck disable=SC2086
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $PKGS \
      || { bad "apt-get install 失败"; exit 1; }
  fi
  ok "系统依赖就绪"
fi

command -v git >/dev/null 2>&1 || { bad "还是没有 git，装不了 sau"; exit 1; }

# ── 2. 克隆 sau ─────────────────────────────────────────────────
step "2/6 获取 social-auto-upload"
if [ -d "$SAU_ROOT/.git" ]; then
  ok "已存在：$SAU_ROOT（不重新克隆）"
else
  note "克隆 $SAU_REPO → $SAU_ROOT"
  git clone --depth 1 "$SAU_REPO" "$SAU_ROOT" || { bad "克隆失败"; exit 1; }
  ok "克隆完成"
fi

# ── 3. 虚拟环境 ─────────────────────────────────────────────────
step "3/6 虚拟环境"
if [ ! -x "$SAU_ROOT/.venv/bin/python" ]; then
  python3 -m venv "$SAU_ROOT/.venv" || {
    bad "建 venv 失败。若提示 ensurepip，说明缺 python3-venv"
    exit 1
  }
fi
PY="$SAU_ROOT/.venv/bin/python"
PIP="$SAU_ROOT/.venv/bin/pip"
"$PIP" install -q --upgrade pip -i "$PIP_MIRROR" >/dev/null 2>&1 || true
ok "venv 就绪（$($PY -V 2>&1)）"

# ── 4. 装 sau ───────────────────────────────────────────────────
step "4/6 安装 sau 及其依赖"
PYVER="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
note "sau 声明 requires-python >=3.10,<3.13，本机是 $PYVER"
note "→ 依赖（loguru/opencv/patchright/requests/qrcode/segno）在 3.13 上都正常，"
note "  所以用 --ignore-requires-python 绕过那个保守上限。"

# 坑 4：--ignore-requires-python
"$PIP" install --ignore-requires-python -i "$PIP_MIRROR" -e "$SAU_ROOT" \
  || { bad "装 sau 本体失败"; exit 1; }
ok "sau 本体已装"

# 坑 5：pyproject 漏了 playwright，但代码里 9 个文件 import 它
if ! "$PY" -c 'import playwright' 2>/dev/null; then
  note "sau 的 pyproject 只列了 patchright，但 uploader 里 import 的是 playwright"
  note "→ 缺它整个 CLI 起不来（sau_cli.py 导入阶段就炸），补装 playwright"
  PW_VER="$("$PY" -c 'import importlib.metadata as m; print(m.version("patchright"))' 2>/dev/null || echo "")"
  # patchright 的版本号跟 playwright 不同步（patchright 1.58.2 ≈ playwright 1.58.0），
  # 所以不能直接拿 patchright 的版本号去装 playwright。
  "$PIP" install -q --ignore-requires-python -i "$PIP_MIRROR" "playwright" \
    || { bad "装 playwright 失败"; exit 1; }
  ok "playwright 已补装（patchright 是 $PW_VER）"
else
  ok "playwright 已存在"
fi

# 坑 6：conf.py
if [ ! -f "$SAU_ROOT/conf.py" ]; then
  cp "$SAU_ROOT/conf.example.py" "$SAU_ROOT/conf.py"
  ok "已从 conf.example.py 生成 conf.py"
else
  ok "conf.py 已存在"
fi

# 顺手把 YouTube 代理和本地 chromium 路径写进 conf.py（只在还是默认值时改）
"$PY" - "$SAU_ROOT/conf.py" "${YT_PROXY:-http://127.0.0.1:7890}" <<'PYEOF'
import sys
from pathlib import Path

path, proxy = Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
changed = []

if "YT_PROXY = None" in text and proxy:
    text = text.replace("YT_PROXY = None", f'YT_PROXY = "{proxy}"')
    changed.append(f"YT_PROXY = {proxy}")

chrome = "/usr/bin/chromium-browser"
if 'LOCAL_CHROME_PATH = ""' in text and Path(chrome).exists():
    text = text.replace('LOCAL_CHROME_PATH = ""', f'LOCAL_CHROME_PATH = "{chrome}"')
    changed.append(f"LOCAL_CHROME_PATH = {chrome}")

if changed:
    path.write_text(text, encoding="utf-8")
    for c in changed:
        print(f"    conf.py: {c}")
PYEOF

# ── 5. 浏览器 ───────────────────────────────────────────────────
step "5/6 浏览器（patchright + playwright 各一套）"
if [ -n "$PLAYWRIGHT_HOST" ]; then
  export PLAYWRIGHT_DOWNLOAD_HOST="$PLAYWRIGHT_HOST"
  note "下载源：$PLAYWRIGHT_HOST"
else
  # 坑 8：npmmirror 的 playwright 镜像已失效（返回阿里云错误 XML）。
  # 官方 CDN 实测 2MB/s，反而更快。
  unset PLAYWRIGHT_DOWNLOAD_HOST || true
  note "下载源：官方 CDN（实测 2MB/s；npmmirror 镜像已失效）"
fi

if ! ls "$HOME/.cache/ms-playwright"/chromium-* >/dev/null 2>&1; then
  "$SAU_ROOT/.venv/bin/patchright" install chromium || note "patchright 的 chromium 装失败（可继续，用系统 chromium 兜底）"
fi
if ! ls "$HOME/.cache/ms-playwright"/chromium-* >/dev/null 2>&1; then
  "$SAU_ROOT/.venv/bin/playwright" install chromium || note "playwright 的 chromium 装失败"
fi

if ls "$HOME/.cache/ms-playwright"/chromium-* >/dev/null 2>&1; then
  ok "chromium 就绪"
  du -sh "$HOME/.cache/ms-playwright" 2>/dev/null | awk '{print "    占用 "$1}'
else
  bad "两套 chromium 都没装上。扫码登录/上传会失败。"
  note "手动重试：$SAU_ROOT/.venv/bin/patchright install chromium"
fi

# ── 6. 自检 ─────────────────────────────────────────────────────
step "6/6 自检"
"$SAU_ROOT/.venv/bin/sau" --help >/dev/null 2>&1 \
  && ok "sau 命令可用" \
  || { bad "sau 命令不可用，跑一下它看报什么错：$SAU_ROOT/.venv/bin/sau --help"; exit 1; }

"$PY" - <<'PYEOF' || true
import asyncio
try:
    from patchright.async_api import async_playwright
except Exception as exc:
    print(f"  ✗ 浏览器自检失败：{exc}")
    raise SystemExit(0)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        v = b.version
        await b.close()
        print(f"  ✓ 浏览器能启动（chromium {v}）")

asyncio.run(main())
PYEOF

cat <<EOF

装完了。下一步：

  1) 装 vp-publish 本体（如果还没装）
       cd ~/vp-publish && ./vp-publish doctor

  2) 登录你要发的平台（每个平台一次，要人扫码）
       推荐用网页，二维码直接显示在浏览器里，过期一键换：
       ./vp-publish login-web
     （不想开网页就还是命令行：）
       ./vp-publish login douyin --headed
       ./vp-publish login xiaohongshu --headed
       ./vp-publish login bilibili --headed

  3) 看看能发到哪
       ./vp-publish doctor

  4) 发
       ./vp-publish 你的视频.mp4

  5) 或者让它自己盯着目录发（推荐，接上流水线）
       ./vp-publish watch ~/vp/videos --once --dry-run   # 先看看会发什么
       ./vp-publish watch ~/vp/videos                    # 常驻
EOF
