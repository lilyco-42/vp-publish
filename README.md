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

> **第一次用先登录**：`./vp-publish login-web` —— 起一个网页，二维码直接显示在
> 浏览器里，过期一键换。详见下面「扫码登录网页」一节。

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
并把结果记进状态文件。

**推荐的使用顺序是「先把服务跑起来，再慢慢登录」** —— watch 在没有已登录平台时
**不会退出**，只会空转等着；你哪天扫码登录了，它下一轮就自动开始工作，
**不用重启服务**。而且这期间它**什么都不登记**，所以那些积压的老视频
在你登录后仍然会被当作库存跳过，不会被一次性发出去。

跑成常驻服务（仓库里带了 `deploy/vp-publish-watch.service`）：

```bash
mkdir -p ~/.config/systemd/user
cp deploy/vp-publish-watch.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now vp-publish-watch
loginctl enable-linger $USER          # 没登录也跑（板子重启后有效）
journalctl --user -u vp-publish-watch -f   # 看它在干什么
# 日志也落在 ~/.local/state/vp-publish/watch.log（故意不写进仓库目录）
```

**三个关键设计**（都是踩过才加的，改代码前先看）：

| 行为 | 为什么 |
|---|---|
| **连续 2 轮大小不变**才认为写完 | 正在写入的视频会先出现半截文件。只判「文件存在」会把半截视频发出去，而且要等平台审核失败才发现。旁边的 `.json` 元数据没写完也一样等。 |
| **库存按文件保护**（首次见到即定） | 如果目录里存了 50 条老视频，一启动全发出去是灾难。要发库存得显式 `--publish-backlog`。**注意这里不能用「首次启动」这种全局开关** —— 见下面第 21 条坑。 |
| **没有已登录平台时不退出** | 退出 + systemd `Restart=always` = **重启死循环**（实测 32 秒内就重启了一次，日志被同一句报错刷满）。而且 cookie 全过期时会自动进入这个状态。空转期间**照常把文件登记为库存**，只是不发 —— 见下面第 26 条坑。 |
| **失败不自动重试** | 一条坏视频不该把后面全部堵住，也不该在平台侧反复触发风控。要重发：`./vp-publish forget <视频>`，下一轮自然会捡起来。 |

常用参数：

```bash
./vp-publish watch ~/vp/videos --once            # 只扫一轮（调试用；无平台时退出码 2）
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

**还有一类失败是 cookie 治不好的**：光有 sau 不够的平台。体检会把它排在
「未登录」前面 —— 因为登录一百次也发不出去：

```
B站           我的B站      ✗ 失效    缺 biliup；约 120 天后过期
YouTube       我的YouTube  ✗ 失效    缺真 Chrome
TikTok        我的TikTok   ✗ 失效    缺 sau 的解释器

  ⚠ biliup（sau 会从 GitHub 自动下载它的二进制，所以需要能连上 GitHub）
  ⚠ 真 Chrome（sau 的 YouTube 上传写死了 channel="chrome"，chromium 顶不上）
  ⚠ sau 的 venv 解释器（TikTok 的驱动脚本要用它跑，playwright 装在里头）
```

这三个依赖是**照着源码核出来的**，不是照文档抄的：
B站 的 biliup 由 `uploader/bilibili_uploader/runtime.py` 从 GitHub Releases
自动下载到 `~/.social-auto-upload/tools/biliup/<系统>-<架构>/`；
YouTube 的 `uploader/youtube_uploader/main.py` 里三处 `launch()` 全写着
`channel="chrome"`；TikTok 的驱动必须用 sau venv 里的 playwright 跑。
JSON 输出里也有（`requires` / `missing` 两个字段）。

### 扫码登录网页（`login-web`）—— 推荐用这个

扫码登录的死穴是**延迟**：二维码有时效（抖音实测**一分钟量级**就失效），
而「命令行出码 → 把图片取出来 → 递给用户 → 用户打开 → 扫」这条路上
每多一次往返就烧掉几秒。更糟的是 **sau 不会自己重出二维码** ——
过期之后它就永远停在「请扫码，小人正在耐心等待登录完成」，只能杀掉重来。

`login-web` 把这条路压到最短：**浏览器里直接就是二维码**，过期一键换新的。

```bash
./vp-publish login-web            # 默认监听 0.0.0.0:8765
```

启动后会打印一个带随机令牌的地址：

```
扫码登录网页已经起来了，在浏览器里打开：
  http://192.168.10.165:8765/?k=Xy7_q2mBv1A
```

**它不会替你在板子上开浏览器** —— 板子是无头的，开也没人看。把打印出来的
地址复制到你自己电脑/手机的浏览器里打开就行（同一个局域网）。

页面上能做的事：

| 操作 | 说明 |
|---|---|
| 点平台卡片 | 立刻拉起 sau 出码，码直接显示在页面上 |
| **换一张** | 杀掉当前会话重出（过期了按这个） |
| **到期自动换** | 默认开：码显示满 `--refresh` 秒自动重出，不用管 |
| 停止 | 收掉 sau 和它拉起的浏览器 |
| 运行日志 | 折叠区，出问题时看这里（终端二维码的方块字符已经被清掉） |

两个实测踩出来的细节，改这里之前先看：

- **过期的码不会当成正常码给你看**。页面判断「这张码还能不能用」用的是
  **服务端算好的 `qr_age`**，不是页面自己数秒 —— 页面可能是刚打开的，
  也可能是睡了一觉的标签页。超过刷新间隔就灰掉并盖上「已经过期，正在换新的…」。
  最初没做这件事：页面打开时那张码已经过期 114 秒，却照样显示得好好的，
  扫了就是「该二维码已过期」。
- **换一张时旧码继续挂着**，新码到了再替换。因为 chromium 起来要十几秒，
  中间空着用户会以为坏了。但**换平台时旧码必须清掉** ——
  否则会出现「页面显示抖音的码、实际在登 B 站」，扫了就是登不上，而且零报错。

**自动换码只在有人看着页面时才发生**（浏览器里的定时器驱动）。
没人看还每 75 秒拉一次 chromium，在板子上是纯浪费。
所以服务停了很久之后再打开页面，看到的会是「已过期，正在换新的…」，
几秒后新码出现。

常用参数：

```bash
./vp-publish login-web --port 8899        # 换端口
./vp-publish login-web douyin             # 起来就直接出抖音的码
./vp-publish login-web --headed           # 显示浏览器窗口（排查用）
./vp-publish login-web --refresh 60       # 自动换码间隔（秒）
./vp-publish login-web --no-token         # 关掉令牌校验（只在完全可信的网络里）
```

**为什么要有令牌**：这个页面能拉起浏览器进程，而 `--host` 默认是 `0.0.0.0`
（不这样手机就访问不到）。同一局域网里别人拿到地址就能操作你的登录会话，
所以默认生成一个随机令牌，地址里带上 `?k=...`。首页本身不需要令牌，
否则你没法把链接发给自己。

**注意**：登录成功靠「账号文件出现」判断，页面会自己变成绿色「登录成功」，
不需要你回命令行看。但如果 sau 进程被提前杀掉，手机上点确认会失败 ——
所以**别在手机上还没点完就关页面**。

### 登录（命令行方式）

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
| `tiktok` | TikTok | 扫码 | — | 3:4 | | | **sau CLI 里没有它**，走 vp-publish 自带驱动（见下） |

「—」表示 sau 文档没写、我们也不猜——不裁剪，交给平台自己处理。
真踩到坑了，在配置里覆盖：

```json
{ "title_max": { "douyin": 55 } }
```

### `tiktok` 为什么不一样

它是唯一一个 `driver != "cli"` 的平台。sau 的命令行里**没有** `tiktok`
这个子命令（实测 `sau tiktok` → `invalid choice: 'tiktok'`，可选值只有前 10 个），
但仓库里 `uploader/tk_uploader/` 的实现是齐的 —— 上游写好了、忘了接进 argparse。

所以 vp-publish 自带一个驱动脚本 `vp_publish/tk_driver.py` 代跑它。
这个脚本**由 sau 的 python 执行**（不是被 import）：

```
<sau venv>/bin/python  <vp_publish/tk_driver.py>  login|upload  ...
```

因为 vp-publish 本身是零依赖的（用系统 python3 就能跑），而驱动需要
playwright —— playwright 只装在 sau 的 venv 里。体检里的「sau 解释器」
那一项就是在检查这个。**定时发布故意没接**（原因见第 39 条坑）。

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

**各平台登录实测现状**（2026-09-27）

先说结论：**这 11 个平台在健康机器上全部可用**。下表是两轮实测合起来的 ——
一轮在 Radxa 板子上（真 SBC），一轮在一台普通 x86 机器上（chromium 145）。
两轮不一致的地方标出来了，那些是**那台板子自己的问题**，不是平台的问题。

| 平台 | 普通机器 | 板子上 | 说明 |
|---|---|---|---|
| 抖音 | ✅ | ✅ 2 秒 | |
| 微博 | ✅ | ✅ 10 秒 | |
| 支付宝生活号 | ✅ | ✅ 24 秒 | |
| 小红书 | ✅ | ✅ | 码文件名是 `_xhs_login_qrcode`（第 34 条坑） |
| 快手 | ✅ | ✅ | 码文件名是 `_ks_login_qrcode` |
| 虎扑 | ✅ | ✅ | 码文件名是 `_qq_qrcode`，**没有时间戳** |
| 视频号 | ✅ | ❌ | 板子上渲染进程崩；同一份代码在普通机器上 1.3 秒出码 |
| 百家号 | ✅ | ❌ | 板子上 `Target crashed`；普通机器正常出码 |
| TikTok | ✅ 3.5 秒 | 未测 | sau CLI 里没有它，走自带驱动（见下） |
| B站 | 要真终端 | 同左 | 走 biliup，硬性要求 `stdin/stdout` 都是 tty（第 35 条坑） |
| YouTube | 要真 Chrome | 同左 | sau 写死了 `channel="chrome"`，chromium 顶不上 |

> ⚠️ **板子上那两个失败不是平台的问题**。那台板子的 SD 卡在同期出现了 ext4
> 元数据损坏（`bad block bitmap checksum`、`This should not happen!! Data will be lost`），
> 文件被大面积写坏 —— 连系统 python 的 ELF 头都被写成了垃圾。
> 也就是说，「这个平台不可用」这个结论**本身是被坏盘污染的**，
> 必须换一台健康机器复核才作数。复核结果就是上表第一列。

**TikTok 是怎么接进来的**（2026-09-27 实测）：

上游那版登录用的是 `await page.pause()`（Playwright Inspector），
**必须有人在图形界面里点「继续」**，无头设备上走不通 —— 所以确实不能照抄。
但「无头取不到码」这个结论是**错的**：

```
· 扫码页 https://www.tiktok.com/login/qrcode 直接就是二维码，不用先点「使用 QR 碼」
· 码画在 <canvas> 上（不是 <img>，拿不到 src）—— 但 canvas 的元素截图走合成器，
  不受 taint 限制，实测截得下来
· 截出来的图用 opencv 能解回 https://www.tiktok.com/t/<id>/ —— 是真码
· 登录态标记：出现 sessionid（扫码前只有 msToken/ttwid 等无关 cookie）
· **码不会自己换**：盯着 canvas 采了 200 秒（40 次），画面一个像素都没变，
  也就是有效期 ≥200 秒。所以不需要任何「检测过期 → 点刷新」的逻辑
```

所以驱动脚本自己写了个无头登录（约 60 行），**不依赖任何 UI 文案或类名**，
比上游那版 `page.pause()` 更稳。实测 `Hub.start("tiktok")` 到出码 **3.5 秒**，
产出 510×510 的 PNG（让浏览器按 3 倍设备像素比渲染，不是把小图拉大 ——
170px 的原始 canvas 手机扫起来很难受）。

**端到端编排**（`--dry-run`，5 个平台）：2.1 秒，各平台 argv 逐个核对无误。

**封面生成**：`1920x1080` / `1080x1440` / `1440x1080`，三个比例各一份，
5 个平台共用（按比例去重）。

> ⚠️ **未验证的一环**：扫码之后的实际上传。这需要人拿手机扫二维码，无法自动化。
> 编排层、参数组装、封面生成、浏览器栈、登录出码全部实测通过；
> 上传动作本身由 sau 完成，它是 15k★ 的成熟项目。
> **TikTok 的上传还要多一层保留**：那段代码上游从没接进 CLI，也就没人端到端跑过，
> 我们只是把它的类调起来（`--dry-run` 能看到完整的 argv）。

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

24. **常驻服务「没有可做的事」时不能退出**。最初 `watch` 在一个平台都没登录时
    直接 `die()`。单独跑看着挺合理（提示清楚、退出码 2），但配上
    systemd 的 `Restart=always` 就是**重启死循环** —— 实测 32 秒内 `NRestarts`
    就从 0 变 1，日志被同一句报错刷满。而且这不是边缘情况：**cookie 全部过期时
    会自动进入这个状态**。正确做法是空转等着（登录后下一轮自动开始工作），
    并且这期间**什么都不登记**，这样积压的老视频在登录后仍受库存保护。

25. **`publish_one_video` 在「目标平台为空」时绝不能返回成功**。否则
    `any_fail` 保持 False → `ok=True` → 视频被记成「已发布」→ **永久跳过，
    而且日志里毫无痕迹**。第 24 条修完就会踩上这个：没有平台时不退出了，
    于是每个新视频都被静默标记成已发。两个坑必须一起修。
    返回里加 `attempted` 字段，调用方据此判断「到底有没有平台真的被尝试过」。

26. **空转期间也必须登记文件，而且要登记成「库存」**。第 24 条改成「不退出、
    空转等着」之后，我顺手把空转期间的文件登记也去掉了 —— 看起来更"干净"，
    实际是个陷阱：服务先上线、几天后才登录，那几天产出的视频会被当成
    **新视频**，一登录就**一次性全发出去**。按一天 4 条 × 10 个平台算，
    几百次上传，平台侧大概率直接判风控。
    取舍标准是**可逆性**：漏发可以补（`--publish-backlog` / `forget` 后重发），
    误发一大批不可逆。所以空转期间照常登记为库存、`baseline_done` 保持 False
    （这样「第一次真正有能力发布的扫描」那一刻目录里已有的东西都算库存），
    只是不发。空转日志会说明「已登记 N 个视频（登录后按库存跳过）」。

27. **改完一个 bug 要顺着它的新行为再走一遍**。第 24、25、26 条是一条链：
    修了「退出」带出「假装发了」，修了「假装发了」又带出「积压被批量发」。
    第一个 bug 是板子上部署 systemd 服务时实测到的，后两个是顺着想下去的。
    只在本地跑单测的话，这三个都不会暴露 —— 单测全绿。

28. **`sau.newest_qr()` 的兜底 glob 不能用在多平台场景**。它为了兼容历史位置
    会 glob **整个 cookies 目录**的 `*.png`。命令行一次只登一个平台时没问题，
    但在网页里就成了陷阱：你会扫到**上一个平台的二维码** ——
    页面看起来完全正常，扫了就是登不上，而且不会有任何报错。
    正确做法是只 glob `cookies/{platform}_{account}_login_qrcode_*.png`，
    宁可「暂时没码」也绝不拿别人的凑数（`loginweb.Hub._qr_for`，
    有 `tests/e2e_loginweb.sh` 第 5 节专门守这条）。

29. **别用从公网 CDN 拉资源的页面生成器**（说的就是 `lilyco-gui`）。
    它生成的页面从 CDN 拉 layui，**且所有事件绑定都包在 `layui.use()` 回调里**：
    板子连不上外网 → `layui is not defined` → 回调不执行 → 按钮退化成原生表单提交，
    表现为**点了没反应，且零报错**。所以 `loginweb.PAGE` 是纯原生 JS、零外部请求，
    并写了单测断言页面里不含 `http://` / `https://` / `cdn.` / `layui`。

30. **校验顺序：调用方的入参错误要排在环境错误前面**。`Hub.start()` 原本先查
    「本机有没有 sau」，后查「平台名认不认识」。结果在一台没装 sau 的机器上
    传个错平台名，得到的是「没找到 sau」——驴唇不对马嘴，排查时会被带偏。
    单测 `test_start_rejects_unknown_platform` 就是这条的哨兵。

31. **端到端脚本里两个「看着对其实错」的写法**：
    · `( cd x && cmd ) &` 的 `$!` 是**子 shell 的 pid**，`kill` 它杀不掉里面的
      python，端口会一直被占着 → 后面起第二个服务直接失败。直接后台跑
      `"$PY" "$REPO/vp-publish" ...` 即可（Python 会把脚本目录加进 `sys.path`，
      与 cwd 无关）。
    · **不能用环境变量控制被测进程的子进程行为** —— 服务进程的环境在它
      启动那一刻就固定了，之后再 `export` 是传不进去的（Python 侧
      `os.environ.copy()` 拿的是服务自己的那份）。要动态控制就用**标记文件**。

32. **「过期的东西」不能当成正常的东西显示出来**。login-web 第一版里，
    页面显示哪张码只看 `qr_mtime` 变没变。结果服务空转了几分钟之后再打开页面，
    那张**已经过期 114 秒**的码被当成正常码显示出来，1 秒后才触发换码 ——
    用户扫到的就是「该二维码已过期」。修法：**能不能用由服务端算好的 `qr_age`
    判断**，超过刷新间隔就灰掉并说明「正在换新的」。
    推广开：凡是「有保质期」的状态（token、缓存、订阅），显示时都要带上
    「现在还有效吗」，而不是「我上次拿到的是什么」。

33. **测试别把「这台机器长什么样」当成前提**。`test_..._even_without_sau`
    第一版直接假设「本机没装 sau」——本机（没装）绿，板子（venv 在 PATH 里）
    红。凡是要构造某种环境状态，就**直接改属性/写临时文件**，
    不要依赖真实机器恰好是那个样子。否则这个测试会随环境飘，
    而且失败时报的是「前提不成立」，看着像环境问题、实际是测试写错了。

34. **二维码的文件名，各平台是各写各的**。`sau.qr_glob` 原本硬编了
    `{platform}_{account}_login_qrcode_*.png` 这个后缀。实测把每个平台都真跑
    一遍才发现，真实文件名是：

    | 平台 | 真实文件名 |
    |---|---|
    | 抖音 | `douyin_我的抖音_login_qrcode_20260927_142027.png` |
    | 微博 | `weibo_我的微博_login_qrcode_....png` |
    | 支付宝 | `alipay_我的支付宝生活号_login_qrcode_....png` |
    | 小红书 | `xiaohongshu_我的小红书_xhs_login_qrcode_....png` ← 多一截 `_xhs` |
    | 快手 | `kuaishou_我的快手_ks_login_qrcode_....png` ← 多一截 `_ks` |
    | 虎扑 | `hupu_我的虎扑_qq_qrcode.png` ← **连时间戳都没有** |

    硬编后缀的后果不是报错，而是**小红书/快手/虎扑三个平台静默失效** ——
    网页上显示「没有二维码」，而码就躺在 cookies 目录里。
    修法：按「`{platform}_{account}` 开头 + 名字里有 `qrcode` + `.png`」匹配
    （前缀要 `glob.escape`，账号名里可能碰巧有 `[`）。
    **教训**：这个 bug 单测抓不到 —— 因为单测是我照着同一个错误假设写的。
    要发现它只有一条路：**真把每个平台跑一遍，去看磁盘上到底生成了什么文件**。

35. **别让用户点一个注定失败的按钮**。B站的登录在网页里做不成 —— sau 底层
    走的是 `biliup`，它硬性要求 `sys.stdin` 和 `sys.stdout` 都是 tty
    （`has_interactive_terminal()`），而网页起子进程时 stdin 是 `DEVNULL`。
    第一版只是让它失败并报一句「登录失败」，用户只会反复点。
    修法：给平台加 `login="terminal"`，网页里**把这张卡片画成不可点的**，
    并直接把该跑的那条命令打出来。同一条规矩的另一半：
    `--headless` **不是每个平台的 login 子命令都认**（bilibili 的只声明了
    `--account`，多给一个会被 argparse 当场打回），所以 argv 要按平台能力组装。

36. **别在一台正在坏的机器上做诊断**。这一轮最大的教训。
    板子的 SD 卡开始坏之后，我拿到的「平台不可用」结论里混进了大量假象：
    视频号崩、百家号崩、缺 Chrome…… 换到一台普通机器上复核 ——
    **同一份代码、同一个 commit**（`0012d2c`），那两个「崩」的平台
    1 秒出头就正常出码了。

    更要命的是当时的证据看起来非常扎实：崩溃日志、未完成的请求列表、
    `--disable-gpu` 对照组…… **全都指向一个不存在的「上游 bug」**。
    差点就照这个结论去给 sau 打补丁了。

    根因是那台板子的根分区 ext4 块位图损坏了（`bad block bitmap checksum`），
    分配器把已占用的块当空闲发出去，于是**写新文件会覆盖掉别人的数据** ——
    连系统 python 的 ELF 头都被写成了 `c2 e7 a6 88`。

    所以：**凡是「某个功能在这台机器上不工作」的判断，先确认机器本身完整**，
    再下结论。三个便宜的哨兵：
    · `dmesg -T | grep -iE 'EXT4-fs error|should not happen'`
    · `tune2fs -l <根分区> | grep -iE 'state|error count'`
    · `dpkg -V`（比官方包多出来的不一致项）
    还有一个免费的：**用 git 当完整性检测器** ——
    `git status` 里冒出你没改过的 modified 文件，基本就是盘坏了。

37. **「命令行里没有」不等于「不支持」**。TikTok 这件事两层都要纠正：
    先是**断言错了** —— `sau tiktok` 报 `invalid choice` 就下结论「上游没做」，
    但 `uploader/tk_uploader/` 里 `TiktokVideo.upload()`、`click_publish()`、
    `detect_upload_status()` 全都在，是完整实现，只是没接进 argparse。
    判断一个能力有没有，**要看 `uploader/` 目录，不能只看 `--help`**。

    然后是**另一个方向的错**：找到实现就以为「照抄即可」。上游那版登录用的是
    `await page.pause()` —— 它拉起 Playwright Inspector，必须有图形界面、
    必须有人手点「继续」。无头设备、远程 SSH、板子上全都没法用。
    所以「上游有实现」的正确用法是**读它的逻辑，自己写能跑的那一层**，
    而不是把它的入口直接调起来。

38. **`executable_path=""` 不是「用自带浏览器」，是「执行 `.`」**。
    sau 的 `conf.py` 里 `LOCAL_CHROME_PATH = ""`，而 `TiktokVideo.upload()`
    把它原样传给 `chromium.launch(executable_path=...)`。实测对照：

    ```
    executable_path=''   -> Error: BrowserType.launch: Failed to launch: spawn . ENOENT
    executable_path=None -> OK
    ```

    playwright 只认 `None` 表示「用自带浏览器」，空字符串会被当成
    **要执行的程序路径**。也就是说上游的 TikTok 上传**开箱即崩**。
    驱动脚本里替它修掉了（`patch_empty_chrome_path()`，有单测守住）。
    这类「空字符串被当成有效值」的坑，在配置驱动的代码里特别常见。

39. **宁可少声明一个能力，也不要让用户的定时发布悄悄变成立即发布**。
    TikTok 的上游实现里有 `set_schedule_time()`，但它依赖 TikTok Studio 的
    英文 UI（`span.tiktok-timepicker-left` 之类）和 `datetime.strptime(month, '%B')`，
    **没有任何端到端验证**。所以 `platforms.py` 里 TikTok 的 `caps`
    **故意不含 `SCHEDULE`**。

    如果声明了，用户在 `--schedule` 里写了「明天 16:00」，而那段代码
    在中文界面上静默失败，视频就**当场发出去了** —— 这比明说「不支持」
    坏得多。这条和标题裁剪那条是同一个原则：**不确定的时候，不替用户做决定**。

40. **环境导致的失败，要用 A/B 对照定性，别急着改代码**。
    本地跑 `e2e_loginweb.sh` 时一堆 `curl: (52) Empty reply from server`，
    看起来像我改坏了分发逻辑。做法是：把 **改动前的版本**
    （`git show HEAD:tests/e2e_loginweb.sh`）用**同样的方式**跑一遍 ——
    它一模一样地失败（同样 7 次 `Empty reply`），于是结论是
    「环境限制」，不是「我引入的 bug」。

    根因有两层，都是 Windows/MSYS 的：假 sau 是个 `#!/bin/sh` 脚本，
    Windows 执行不了（`WinError 193`），所以 `Popen` 抛异常、请求没有响应；
    另外 Windows 自带的 curl 不认 MSYS 的 `/dev/null`
    （`curl: (23) client returned ERROR on write`）。
    那个 e2e 本来就是给 Linux CI 写的。

    **先证伪「是我改坏的」，再去找真原因** —— 否则会去修一个不存在的问题，
    还会把本来正确的代码改错。

---

## 目录结构

```
vp-publish/
├── vp-publish                 # 入口（免安装，直接跑）
├── publish-all                # 同一个东西的别名
├── bootstrap.sh               # 一键装环境
├── deploy/
│   └── vp-publish-watch.service  # systemd 用户服务（守护模式常驻）
├── vp_publish/
│   ├── cli.py                 # 命令行、发布主流程
│   ├── platforms.py           # 平台能力矩阵（照着 argparse 核出来的）
│   ├── meta.py                # 元数据推导 + 按平台适配
│   ├── sau.py                 # sau 后端封装（唯一知道 sau 长什么样的地方）
│   ├── cover.py               # ffmpeg 抽帧 + 按比例裁剪
│   ├── doctor.py              # 体检
│   ├── state.py               # 幂等记录
│   ├── watch.py               # 守护模式：等文件写完、库存保护、失败不重试
│   ├── loginweb.py            # 扫码登录网页（零外部请求，过期一键换）
│   ├── tk_driver.py           # TikTok 驱动（sau CLI 里没有它，用 sau 的 python 跑）
│   ├── config.py              # 配置（零依赖 JSON）
│   └── report.py              # 表格渲染（含中文宽度）
└── tests/
    ├── test_vp_publish.py     # 129 项单元测试
    ├── e2e_watch.sh           # watch 跨轮行为演练（假 sau）
    ├── e2e_loginweb.sh        # 扫码网页演练（真 HTTP + 真 PNG）
    ├── reach_probe.py         # 实测各平台可达性
    └── reach_proxy.py         # 实测 YouTube 走代理
```

---

## 测试

```bash
python3 -m unittest discover -s tests -v   # 129 项单元测试
bash tests/e2e_watch.sh                    # watch 跨轮行为演练（假 sau，几秒跑完）
bash tests/e2e_loginweb.sh                 # 扫码网页演练（真起 HTTP 服务，约半分钟）
```

单元测试覆盖平台别名解析、argv 组装、元数据推导、标题裁剪、封面比例计算、
账号发现、cookie 过期估算、中文表格对齐、状态幂等，以及二维码路径解析
（防第 16/17 条坑回归，含**六种真实文件名**与「前缀放宽后仍然平台严格」，
防第 34 条坑回归）、watch 的就绪判定/库存登记/无平台不记状态、
**平台额外依赖的判定**（biliup / 真 Chrome，防第 36 条坑那类
「看着绿其实发不出去」回归），
以及扫码网页的**平台严格匹配**、**零外部请求**、**换码/换平台时旧码的去留**、
**注定做不成的平台要被拒**（第 35 条）、
HTTP 令牌与路由（防第 28/29/30/32/33 条坑回归）。

`tests/e2e_watch.sh` 是**跨轮**行为的演练 —— watch 的坑全在「第 N 轮和第 N+1 轮
之间」，单测覆盖不到：库存保护、等文件写完、幂等、失败不重试、dry-run 不脏状态、
forget 后能重发，以及**「服务先上线、后登录」**（不退出、不脏状态、库存仍受保护、
登录后免重启自动开始、空转期间按库存登记）。共 39 条断言。它用假 sau，不碰网络、不发任何东西，**CI 里也跑**。
第 21 条坑就是它抓出来的。

`tests/e2e_loginweb.sh` 真起一个 HTTP 服务、真发请求、真拿二维码 PNG
（假 sau 会按 sau 的真实命名规则生成一张**合法** PNG，并把「哪个平台/哪个账号」
写进 tEXt 块，这样可以直接 grep 二进制确认拿到的是谁）。共 34 条断言，
最要紧的四条是**「别的平台的码更新，也不能递给我」**（第 28 条坑）、
**「换一张时旧码继续挂着」**、**「换平台时旧码必须清掉」**、
**「小红书/快手/虎扑那种各写各的文件名后缀都要认得出来」**（第 34 条坑）。**CI 里也跑**。

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
