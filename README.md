# vp-publish —— 一键上传视频到各个平台

一条命令，把同一个视频发到**抖音 / 小红书 / 快手 / B站 / 视频号 / 微博 / 虎扑 / 百家号 / 支付宝生活号 / YouTube**。

```bash
./vp-publish 我的视频.mp4
```

就这样。不用写配置文件，不用开开关，不用记住每个平台参数有什么不一样。

---

## 为什么需要这个东西

上游已经有两个成熟项目把「最难的部分」解决了：

| 项目 | 干什么 | 为什么不能直接用 |
|---|---|---|
| [social-auto-upload](https://github.com/dreammis/social-auto-upload)（15k★, MIT） | 浏览器自动化：扫码登录、创作者后台上传、过风控 | 一次只能发一个平台，且每个平台参数不同 |
| [biliup](https://github.com/biliup/biliup)（B站原生二进制） | B站投稿 | 只管 B站 |

它们都是**单平台**工具。真正的痛点在中间那层——**编排**：

- 我到底登录了哪些平台？（sau 要你手工改 `enabled: true`）
- 每个平台标题能写多长？（微博 30 字，虎扑 4~40 字，YouTube 100 字，其余各不同）
- 每个平台封面要什么比例？（抖音 3:4 竖 + 4:3 横，B站 16:9）
- B站必须传分区 id，虎扑**没有**定时发布参数——忘了就被 argparse 打回
- 第 7 个平台失败时，前 6 个已经发出去了，怎么不重发？

vp-publish 就是这一层。**它不碰浏览器、不碰上传协议**——那些是 sau 的活，
而且它比我写得好。它只做编排。

---

## 安装

### 方式一：一键脚本（推荐，在板子上跑）

```bash
git clone <本仓库> ~/vp-publish
cd ~/vp-publish
bash bootstrap.sh
```

`bootstrap.sh` 会把 sau、两套 chromium、ffmpeg、系统库全部装好。
它里面每一步都对应一个**真机上踩过的坑**，见文件头部注释。

只体检不安装：

```bash
bash bootstrap.sh --check
```

### 方式二：手动

```bash
# 1. 系统依赖
sudo apt-get update && sudo apt-get install -y git python3-venv ffmpeg \
  libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
  libdrm2 libgbm1 libasound2t64 libxkbcommon0 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2

# 2. sau（注意 --ignore-requires-python，见下面「踩过的坑」）
git clone --depth 1 https://github.com/dreammis/social-auto-upload.git ~/sau
cd ~/sau && python3 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --ignore-requires-python -e .
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple playwright
cp conf.example.py conf.py

# 3. 浏览器（两套，patchright 和 playwright 各要一份）
.venv/bin/patchright install chromium
.venv/bin/playwright install chromium
```

vp-publish 本体**不需要安装**——`./vp-publish` 直接就能跑，
因为它零运行时依赖（只用 Python 标准库）。

想装到 PATH 里也行：

```bash
pip install -e .          # 之后可以用 vp-publish / publish-all
# 或者
ln -s "$PWD/vp-publish" ~/bin/vp-publish
```

---

## 用法

### 发一个视频

```bash
./vp-publish 我的视频.mp4
```

自动做这些事：

1. 扫 `~/sau/cookies/` 看你登录了哪些平台
2. 从视频旁边找元数据（`我的视频.json` / `meta.json` / `我的视频.txt`），没有就用文件名当标题
3. 按每个平台的规矩裁标题、选封面比例、补必填参数
4. 逐个平台上传，一个失败不影响其他
5. 记下结果，**下次重跑会跳过已成功的**

### 常用参数

```bash
# 只发指定平台（认中文名、英文名、常用缩写）
./vp-publish v.mp4 --only 抖音,B站,xhs

# 跳过某个平台
./vp-publish v.mp4 --skip youtube

# 指定标题/简介/标签/封面
./vp-publish v.mp4 -t "标题" -d "简介" -T "AI,科技" -c cover.png

# 定时发布（只有抖音/快手/小红书/B站/视频号支持）
./vp-publish v.mp4 --schedule "2026-03-24 21:30"

# 先看看会执行什么，不真发
./vp-publish v.mp4 --dry-run

# 重发（忽略「已发过」记录）
./vp-publish v.mp4 --force

# 批量：整个目录的视频
./vp-publish ~/vp/videos/

# 给脚本/agent 用的 JSON 输出
./vp-publish v.mp4 --json
```

### 守护模式：新视频自动发（`watch`）

这是**把 vp-pipeline 的「话题 → 成片 → 发布」补完整**的那一环。
流水线每天定时产出视频，但发布得靠人记得去点 —— watch 模式把它接上：

```bash
./vp-publish watch ~/vp/videos           # 盯着目录，出现新视频就自动发
./vp-publish watch                       # 目录不写就用配置里的 watch_dirs
```

它每 30 秒扫一次目录，发现**写完了**的新视频就发到所有已登录平台，
并把结果记进状态文件。跑成常驻服务：

```bash
# systemd 用户服务（推荐，重启后自动拉起）
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/vp-publish-watch.service <<'EOF'
[Unit]
Description=vp-publish watch
After=network-online.target

[Service]
ExecStart=%h/vp-publish/vp-publish watch %h/vp/videos --log %h/vp-publish/watch.log
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now vp-publish-watch
loginctl enable-linger $USER          # 没登录也跑（板子重启后有效）
```

**三个关键设计**（都是踩过才加的，改代码前先看）：

| 行为 | 为什么 |
|---|---|
| **连续 2 轮大小不变**才认为写完 | 正在写入的视频会先出现半截文件。只判「文件存在」会把半截视频发出去，而且要等平台审核失败才发现。旁边的 `.json` 元数据没写完也一样等。 |
| **首次启动只登记、不发布** | 如果目录里存了 50 条老视频，一启动全发出去是灾难。要发库存得显式 `--publish-backlog`。 |
| **失败不自动重试** | 一条坏视频不该把后面全部堵住，也不该在平台侧反复触发风控。要重发：`./vp-publish forget <视频>`，下一轮自然会捡起来。 |

常用参数：

```bash
./vp-publish watch ~/vp/videos --once            # 只扫一轮（调试用）
./vp-publish watch ~/vp/videos --dry-run         # 看会发什么，不真发
./vp-publish watch ~/vp/videos --publish-backlog # 连库存一起发
./vp-publish watch ~/vp/videos --interval 60     # 轮询间隔
./vp-publish watch ~/vp/videos --stable-rounds 3 # 更保守：稳定 3 轮才发
./vp-publish watch ~/vp/videos --only 抖音,b站   # 只发指定平台
./vp-publish watch ~/vp/videos --log watch.log   # 同时写日志文件
```

状态文件在 `~/.local/state/vp-publish/watch.json`（`--state` 可改）。
它和发布记录（`records.json`）是**两份**：一份记「文件写完了没」，
一份记「发到哪个平台了」。想彻底重来就删掉它们。

> 为什么不做成「流水线跑完调一次 publish」？因为那样就绑死了视频必须由流水线产出。
> 盯着目录更松：流水线产的、你手工拷的、scp 过来的，一视同仁。

### 体检

```bash
./vp-publish doctor              # 秒级，只看本地
./vp-publish doctor --live       # 实连每个平台验活（慢但准）
```

输出长这样：

```
环境
────────────────────────────────────────────────────────────
  ✓ sau            /home/radxa/sau/.venv/bin/sau
  ✓ ffmpeg          可用（自动生成封面）
  ✓ 浏览器          chromium-1208, chromium_headless_shell-1208
  ✓ 代理            http://127.0.0.1:7890 可达

平台（离线判断，加 --live 可实连验活）
────────────────────────────────────────────────────────────
平台          账号         状态      说明
────────────────────────────────────────────────────
抖音          我的抖音     ✓ 就绪    约 60 天后过期
小红书        我的小红书   ✓ 就绪    ⚠ 只剩 2.0 天
快手          —            · 未登录  还没登录
B站           我的B站      ✓ 就绪    约 120 天后过期
...
YouTube       我的YouTube  ✓ 就绪    约 30 天后过期

可以直接发：5/10 个平台 —— 抖音, 小红书, B站, 微博, YouTube
```

**「只剩 2.0 天」这行是重点。** 多平台发布的失败，大多数不是上传那一刻才发生的，
而是那个平台的 cookie 三天前就过期了。体检把这 10 分钟的排查提前到 3 秒。

### 登录

每个平台登录一次（**要人操作**，扫码或输账号）：

```bash
./vp-publish login douyin --headed
./vp-publish login xiaohongshu --headed
./vp-publish login bilibili --headed
./vp-publish login youtube --headed     # Google 账号，不是扫码
```

`--headed` 会显示浏览器窗口，**推荐加上**——出问题时你能直接看到卡在哪一步。
登录过程中生成的二维码图片路径会打印出来。

看已登录的账号：

```bash
./vp-publish accounts
```

### 其他

```bash
./vp-publish platforms          # 列出所有平台及其能力（标题长度/封面/定时/合集）
./vp-publish init               # 生成配置文件模板
./vp-publish forget v.mp4       # 清掉某个视频的发布记录
```

---

## 平台能力对照表

`./vp-publish platforms` 的实时输出，这里放一份便于查阅：

| key | 平台 | 登录 | 标题长度 | 封面 | 定时 | 合集 | 备注 |
|---|---|---|---|---|---|---|---|
| `douyin` | 抖音 | 扫码 | — | 横 4:3 + 竖 3:4 | ✓ | ✓ | |
| `xiaohongshu` | 小红书 | 扫码 | — | 3:4 | ✓ | | |
| `kuaishou` | 快手 | 扫码 | — | 3:4 | ✓ | ✓ | |
| `bilibili` | B站 | 扫码 | — | 16:9 | ✓ | | **必须传 `--tid`**，默认 171 |
| `tencent` | 视频号 | 扫码 | — | 横 4:3 + 竖 3:4 | ✓ | ✓ | |
| `weibo` | 微博 | 扫码 | ≤30 | 3:4 | | ✓ | 封面建议 <5MB |
| `hupu` | 虎扑 | 浏览器 | 4~40 | 3:4 | | | 登录要输 QQ/手机号 |
| `baijiahao` | 百家号 | 扫码 | — | 3:4 | | ✓ | |
| `alipay` | 支付宝生活号 | 扫码 | — | 3:4 | | ✓ | 需先开通生活号权限 |
| `youtube` | YouTube | 浏览器 | ≤100 | 16:9 | | | **必须挂代理**；支持播放列表/可见性 |

「—」表示 sau 文档没写、我们也不猜——不裁剪，交给平台自己处理。
真踩到坑了，在配置里覆盖：

```json
{ "title_max": { "douyin": 55 } }
```

---

## 配置

`~/.config/vp-publish/config.json`（可选，**所有字段都有能用的默认值**）：

```bash
./vp-publish init      # 生成模板
```

```json
{
  "sau": {
    "root": "~/sau",
    "bin": "~/sau/.venv/bin/sau",
    "headless": true,
    "accounts": { "douyin": "我的抖音" }
  },
  "default_tags": ["AI", "科技"],
  "tid": 171,
  "visibility": "public",
  "proxy": "http://127.0.0.1:7890",
  "cover": true,
  "cover_at": 1.0,
  "timeout": 900,
  "retries": 0,
  "title_max": {}
}
```

用 JSON 而不是 YAML，是因为 sau 的 venv 里没有 PyYAML——
而本工具刻意设计成「用系统 python3 就能跑」，这样它坏了不连累 sau，
sau 升级了也不连累它。

---

## 设计取舍

### 封面自动生成，按**比例**缓存

没给封面时，用 ffmpeg 从视频第 1 秒抽一帧，裁成平台要的比例。

为什么是第 1 秒不是第 0 帧：很多视频开头是纯黑或淡入，第 0 帧基本是黑的。

关键细节：比例是**平台无关**的——抖音的竖版 3:4 和微博的 3:4 是同一张图。
所以按比例缓存，不按平台。实测 5 个平台从生成 6 个文件降到 3 个：

```
之前：3x4-douyin.png  3x4-weibo.png  3x4-xiaohongshu.png  ← 三个一模一样的文件
现在：3x4.png                                              ← 一个，三个平台共用
```

生成出来的尺寸实测：`1920x1080` / `1080x1440` / `1440x1080`。

### 标题裁剪会说清楚

按平台长度裁剪时**一定打印警告**，告诉你原标题和裁后结果。
悄悄改用户标题比报错更糟。不想被裁就自己改短，或者在配置里调大上限。

### 参数值走 argv 数组，绝不拼 shell 字符串

标题里有 `$`、引号、反引号、emoji 都不会出问题。
（这是最容易埋的坑：`f"{cmd} --title {title}"` 一遇到引号就炸。）

### 幂等：重跑不重发

发布记录存 `~/.local/state/vp-publish/records.json`，**不写进视频目录**。
理由：视频目录可能是只读挂载、可能被 rsync 同步、可能被清理脚本扫到。
状态文件不该混在内容里。

指纹用「路径 + 大小 + mtime」，**不做内容哈希**——10GB 的视频读一遍要几分钟，
而这三个值已经足够回答「这是不是刚才那个文件」。

### 失败隔离

单个平台失败不影响其他平台，最后给一张汇总表。
退出码：全成功 0，有失败 1。可以直接接在流水线里。

---

## 实测数据

板子：Radxa Cubie A7A（全志 A733，8 核，3.8GB RAM），Debian 13 trixie，Python 3.13.5。

**各平台创作者后台可达性**（patchright chromium 145 headless，从板子实测）：

| 平台 | 结果 |
|---|---|
| 抖音创作者中心 | HTTP 200 / 2.0s |
| B站创作中心 | HTTP 200 / 1.4s |
| 小红书创作服务平台 | HTTP 200 / 1.8s |
| 视频号助手 | HTTP 200 / 2.5s |
| YouTube Studio | 直连超时 45s → **走 `http://127.0.0.1:7890` 代理 HTTP 200 / 1.5s** |

**浏览器启动**：patchright 和 playwright 都能拉起 `chromium 145.0.7632.0`。

**登录路径**（`./vp-publish login douyin`，在伪终端里实测）：

```
2026-09-27 13:24:20 | INFO: 🖼️ 二维码已经准备好啦，已保存到:
    /home/radxa/sau/cookies/douyin_测试账号2_login_qrcode_20260927_132420.png

请使用抖音APP扫描下方二维码登录：
 ▄▄▄▄▄ █▀▀▄█  ▄▀  █▄▀█ ▄▄█  ▀█▄█▀██▄▄▀▀▄  ▄▀▄▀▀ █▄▀▀▀▀█▄▀█ ▄▄▄▄▄
 █   █ █▀▀███▄  ▀▀█▄█▀█ ▄ █▀▀ ▀▄▄▀ ▀▀▀▀ ▄ █▀▄█ ▀▀  █▄▀ ▀▄█ █   █
 ...
2026-09-27 13:24:20 | INFO: 🧍 请扫码，小人正在耐心等待登录完成
```

二维码直接打印在终端里，PNG 也落在 sau 声明的路径。
**这一环的坑见下面「踩过的坑」第 16 条**——早期版本因为捕获了输出，
二维码被吞掉，用户在终端上什么都看不到。

**端到端编排**（`--dry-run`，5 个平台）：2.1 秒，各平台 argv 逐个核对无误。

**封面生成**：`1920x1080` / `1080x1440` / `1440x1080`，三个比例各一份，
5 个平台共用（按比例去重）。

> ⚠️ **未验证的一环**：扫码之后的实际上传。这需要人拿手机扫二维码，无法自动化。
> 编排层、参数组装、封面生成、浏览器栈、登录出码全部实测通过；
> 上传动作本身由 sau 完成，它是 15k★ 的成熟项目。

---

## 踩过的坑（改代码前先看）

这一节是给未来的自己看的。每条都在真机上复现过。

### sau 相关

1. **sau 的 `pyproject.toml` 声明 `requires-python = ">=3.10,<3.13"`，但板子是 3.13.5**
   → pip 直接拒绝安装。依赖（loguru / opencv-python / patchright / requests /
   qrcode / segno）在 3.13 上其实都正常，只是上游没测过。
   必须 `--ignore-requires-python`。

2. **sau 的 `pyproject.toml` 只列了 `patchright`，但 9 个文件 `import playwright`**
   （weibo / hupu / tiktok / xhs / alipay / baijiahao 的 uploader，加 `myUtils/login.py`、
   `myUtils/auth.py`）。而 `sau_cli.py` 在**导入阶段**就 import `baijiahao_uploader`，
   所以缺 playwright 时**整个 CLI 起不来**，报的错还指向百家号，容易误判。
   必须手动补装 `playwright`。

3. **patchright 和 playwright 的版本号不同步**：patchright 1.58.2 ≈ playwright 1.58.0。
   不能拿 patchright 的版本号去 `pip install playwright==1.58.2`（那个版本不存在）。

4. **sau 的 CLI 需要 `conf.py`**，仓库里只有 `conf.example.py`，要手动复制。

5. **`sau bilibili` 的 `--desc` 和 `--tid` 都是 `required=True`**（不是可选！）。

6. **虎扑既没有 `--schedule` 也没有 `--collection`**；微博/支付宝有 `--collection` 但没有 `--schedule`。
   给不支持的平台传这些参数 → argparse 直接报错。

7. **`PUT /configs` 类接口要注意 `?force=true`**（这是 mihomo 的坑，但同理：
   上游 API 经常有「不加参数就静默忽略」的行为，改完必须验回来）。

### 环境相关

8. **板子的 `/var/lib/apt/lists/` 可能是空的**（0 个 Packages 文件）。
   症状很迷惑：`apt-cache policy` 里连**已安装**的包都没有候选版本，
   `apt-get install` 什么都装不上。**先 `apt-get update`**，别假设它跑过。

9. **板子没有 `git`**（radxa 的 Debian 镜像默认不带）。

10. **`python3 -m venv` 报 `ensurepip is not available`** → 缺 `python3-venv` 包。
    注意：`python3 -c "import venv"` 是能成功的，这个检查**测不出问题**。

11. **PyPI 直连在板子上不通**（`https://pypi.org/simple/` 超时无响应），
    但清华/阿里镜像通（200/4.2s），`files.pythonhosted.org` 也通。pip 必须走镜像。

12. **npmmirror 的 playwright 镜像路径已失效**：
    `https://npmmirror.com/mirrors/playwright/builds/chromium/1208/...` 返回阿里云错误 XML。
    **官方 CDN 反而更快**：`cdn.playwright.dev` 实测 2MB/s，
    最终 302 到 `playwright.download.prss.microsoft.com`。
    所以 `PLAYWRIGHT_DOWNLOAD_HOST` 应该**留空**，不要设 npmmirror。

13. **板子自带的 `chromium-browser` 可能是坏的**：
    `chromium-browser --version` 报 `libnss3.so: cannot open shared object file`。
    装了 `libnss3 libnspr4` 等运行库后才能用（版本 120，偏老）。
    但 sau 用的是自己的 chromium 145，所以这个只影响「用系统 chromium 兜底」的路径。

### 本工具相关

14. **argparse 里 `nargs="*"` 的位置参数和子命令不能共存**。
    第一个位置参数会被拿去匹配子命令名，于是 `vp-publish final.mp4` 报
    `invalid choice: 'final.mp4'`。解决：手动分派——先看 `argv[0]` 是不是已知子命令。

15. **中文表格对齐要自己算显示宽度**。`len("抖音")` 是 2，但它占 4 列。
    不处理的话表格全歪。见 `report.width()`。

16. **登录时绝不能捕获子进程输出**（这条是实测踩出来的，且很隐蔽）。
    sau 登录会把二维码用 Unicode 方块字符**打印到 stdout**
    （见 sau 的 `utils/login_qrcode.py: print_terminal_qrcode`）。
    如果用 `subprocess.run(capture_output=True)`，二维码就被吞进变量里了——
    用户在终端上**什么都看不到**，只能干等到超时，而且没有任何报错。
    修法：登录走 `run_stream()`，`stdin/stdout/stderr` 全部透传。
    成功与否改看「账号文件有没有被创建/更新」。

17. **二维码落盘路径是带时间戳的**：
    `cookies/{platform}_{account}_login_qrcode_{YYYYmmdd_HHMMSS}.png`
    （来自 sau 的 `build_login_qrcode_path`）。
    早期版本去找 `cookies/qrcode.png`，永远找不到。
    要用 glob 取最新的那个。

18. **SSH heredoc 里写含嵌套引号的 Python 会炸**（转义层数太多）。
    改成「本地写文件 → scp 过去」两步法，可靠得多。

19. **想在 SSH 里模拟交互终端**：`script -qec "命令" /dev/null` 可以造一个伪终端。
    有些工具（sau 的短信验证码提示）会检查 `sys.stdin.isatty()`，
    没有伪终端就走非交互分支。

20. **watch 里「已处理过」的判断必须放在计数之前**。两个后果：
    ① 每轮都会把已发布的视频重新数成「就绪」，日志被 `扫描 N` 刷满；
    ② `--dry-run` 会把 `published` 状态写进状态文件 —— 试运行一次之后，
       真跑起来就一条都不发了（全都「已处理」）。所以 dry-run 只打印、不落盘。

21. **库存保护必须按文件记，不能用一个全局开关**。这是本项目最凶的一个 bug：
    文件要「连续 2 轮大小不变」才算写完，所以第一轮扫描时**所有老视频都还没就绪**；
    而如果「首次启动」是个全局标志，它会在第一轮结束时就翻过去 ——
    于是第二轮老视频集体变成「就绪」，保护却已经关了，**整个目录的存货被一次性发出去**。
    正确做法：在**第一次见到某个文件**时就把「它当时是否已存在」记在这个文件头上。

22. **`forget` 必须把两份记录都清掉**。发布记录有两份：`state.Store`（发到哪个平台了）
    和 `watch.Store`（这个文件处理过了）。只清一份的话，README 里承诺的
    「forget 之后下一轮自然会捡起来」在 watch 模式下不成立。
    另外 `forget` 和 `watch` 必须认同一个 `--state`，否则自定义状态文件后就清不到。

23. **别指望从子进程的 stdout 判断「到底调没调 sau」**。上传走的是
    `capture_output=True`，子进程输出全被吞掉。写端到端测试时要让假 sau
    **写一个调用日志文件**，再从日志判断 —— 第一版 e2e 就是因为 grep stdout
    而全线假绿。

---

## 目录结构

```
vp-publish/
├── vp-publish                 # 入口（免安装，直接跑）
├── publish-all                # 同一个东西的别名
├── bootstrap.sh               # 一键装环境
├── vp_publish/
│   ├── cli.py                 # 命令行、发布主流程
│   ├── platforms.py           # 平台能力矩阵（照着 argparse 核出来的）
│   ├── meta.py                # 元数据推导 + 按平台适配
│   ├── sau.py                 # sau 后端封装（唯一知道 sau 长什么样的地方）
│   ├── cover.py               # ffmpeg 抽帧 + 按比例裁剪
│   ├── doctor.py              # 体检
│   ├── state.py               # 幂等记录
│   ├── watch.py               # 守护模式：等文件写完、库存保护、失败不重试
│   ├── config.py              # 配置（零依赖 JSON）
│   └── report.py              # 表格渲染（含中文宽度）
└── tests/
    ├── test_vp_publish.py     # 77 项单元测试
    ├── e2e_watch.sh           # watch 跨轮行为演练（假 sau）
    ├── reach_probe.py         # 实测各平台可达性
    └── reach_proxy.py         # 实测 YouTube 走代理
```

---

## 测试

```bash
python3 -m unittest discover -s tests -v   # 77 项单元测试
bash tests/e2e_watch.sh                    # 端到端演练（假 sau，几秒跑完）
```

单元测试覆盖平台别名解析、argv 组装、元数据推导、标题裁剪、封面比例计算、
账号发现、cookie 过期估算、中文表格对齐、状态幂等，以及二维码路径解析
（防第 16/17 条坑回归）。

`tests/e2e_watch.sh` 是**跨轮**行为的演练 —— watch 的坑全在「第 N 轮和第 N+1 轮
之间」，单测覆盖不到：库存保护、等文件写完、幂等、失败不重试、dry-run 不脏状态、
forget 后能重发。它用假 sau，不碰网络、不发任何东西，**CI 里也跑**。
第 21 条坑就是它抓出来的。

不覆盖「真实上传」——那个需要人扫码，见「实测数据」一节。

---

## 与 vp-pipeline 的关系

[vp-pipeline](../vp-pipeline) 是「话题 → 文案 → 配音 → 画面 → 成片」的生产线。
它的最后一步（发布）原本是内嵌的 `publish.py`，只支持 biliup + sau 的简单封装。

vp-publish 把这一层独立出来了，vp-pipeline 的发布步骤可以直接换成：

```bash
./vp-publish "$VIDEO" --json
```

但更省事的做法是**根本不改流水线** —— 直接让 watch 模式盯着它的产物目录：

```bash
./vp-publish watch ~/vp/videos
```

这样流水线只管生产，发布交给守护进程。两边解耦：流水线改目录结构、
换成别的工具产出视频，发布这侧都不用动。

好处：发布逻辑可以单独升级、单独测试，不用碰整条流水线。

---

## 许可

MIT
