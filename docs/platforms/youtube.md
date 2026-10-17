# YouTube MVP

## 支持范围

支持公开视频的 watch、youtu.be、Shorts、live 录播、embed 单视频链接（含 www / m / music.youtube.com）。只在本机识别域名、路径和 11 位视频 ID，随后统一为 `https://www.youtube.com/watch?v={id}`。分享参数、播放列表参数和起播时间不影响来源标识，始终转写完整视频。

不处理纯播放列表、频道、正在直播、预约或录播尚未处理完成的视频；不保证年龄限制、会员、私密、登录受限或遭到平台风控的视频可用。MVP 不包含自动翻译、代理池、Cookie 池和 PO Token 服务。

## 工作流程

1. `youtube-transcript-api` 列出原始字幕，按以下顺序选择有效片段：人工中文（zh-Hans、zh-CN、zh、其他中文变体）、人工英文、自动中文、自动英文、其他原始语言（人工优先）。不会调用自动翻译。
2. `start + duration` 转成 `{start, end, text}`，过滤无效时间/空文本，再用 `normalize_segments` 归一化。有字幕就跳过下载、ffmpeg 和 ASR。
3. 通过 YouTube oEmbed 尽力补标题/作者（5 秒超时）。失败仍保存字幕，标题回退 `youtube-{id}`。字幕路径的 duration 为 null，metadata 记录 `is_generated` 和 `subtitle_language`。
4. 无字幕或字幕请求异常时，由现有 Pipeline 回退 ASR；先查完整 16k WAV 缓存，未命中才调用 `yt-dlp`。`--force-asr` / 详情页换模型重转写跳过字幕。
5. yt-dlp 先检查视频状态，再以 `bestaudio/best` 下载单视频到 `downloads/youtube-{id}.{ext}`，优先纯音频；无独立音轨时允许带画面的媒体回退。返回标题、作者、时长等元数据。ffmpeg 转完整 16k 单声道 WAV 后按 YouTube 视频 ID 缓存，其他平台相同字符串的 ID 不混用。
6. 接入现有任务进度、协作式中断、入库与 txt/plain/md/srt 导出。浏览器可复制原链接，点击文字稿时间戳跳到 YouTube 对应位置。

字幕网络请求默认超时 15 秒；yt-dlp socket 超时 15 秒、下载/提取各最多重试 2 次（多个请求累积耗时可超过 15 秒）。调用期间无法立刻中断时，返回后检查取消令牌并丢弃结果。

## 安装与运行

```bash
uv sync --extra faster-whisper --extra web --extra douyin
uv run video2text tx "https://youtu.be/dQw4w9WgXcQ"
uv run video2text tx "https://youtu.be/dQw4w9WgXcQ" --force-asr
```

基础依赖已含 `youtube-transcript-api`、`yt-dlp[default,deno]`；后者提供与 yt-dlp 匹配的 EJS 和 Deno（含 Docker），不需要手动安装 Node 或 Google API Key。可用 `uv run deno --version` 检查 JS 运行时。默认 ASR 的语言沿用已有配置；英文/多语视频需要 ASR 时，可把 `config.json` 的 `faster_whisper.language` 设置为 null 以自动检测。

## 网络与 Cookie

- YouTube 字幕、oEmbed 和音频默认沿用系统代理，支持 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。`V2T_USE_PROXY` 只控制 B站，不会关闭 YouTube 代理。
- yt-dlp Cookie 顺序：`V2T_COOKIE_FILE` → 工作区 `cookies.txt` → `V2T_COOKIES_FROM_BROWSER`（如 `chrome`）。字幕库不读取这些 Cookie，字幕被拦截时会回退 yt-dlp 音频路径。
- Docker 无法读取宿主机浏览器登录态，可挂载 Netscape 格式 Cookie 到 `/data/cookies.txt`。容器访问宿主机代理需要可达的宿主机地址，不能用容器自己的 `127.0.0.1`。
- 网络超时先确认本机/容器能访问 YouTube。403、bot 验证等可以先更新 `uv lock --upgrade-package yt-dlp` 并以完整 extras 组合重新 `uv sync`；Cookie 也不保证绕过所有限制，不自动配置 PO Token。

实现依据：[youtube-transcript-api 官方接口](https://github.com/jdepoix/youtube-transcript-api)、[yt-dlp EJS 与运行时说明](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。

## 测试与验收

自动化只 mock 外部请求，不访问 YouTube：

```bash
uv run pytest tests/test_youtube_inputs.py tests/test_youtube_downloader.py tests/test_youtube_integration.py -q
uv run pytest -q
(cd web && npm run build)
V2T_BROWSER_TEST_URL=http://127.0.0.1:5174 uv run pytest tests/test_web_browser.py -q
```

专项覆盖链接规范化/拒绝、字幕排序和时间戳、空字幕与网络异常回退、音频下载与元数据、Cookie/代理、中断、未完成直播、缓存隔离/复用、Web API 入库/SRT/重转写及浏览器提交/平台标识/复制/时间戳链接。

2026-10-17 自动化结果：完整 pytest 为 229 passed / 20 skipped（默认跳过浏览器）；另行运行浏览器回归为 20 passed。前端构建、`uv lock --check` 与 `uv run deno --version` 均通过。保留 3 条基线已有的第三方弃用警告。

2026-10-17 手动网络冒烟：`dQw4w9WgXcQ` 字幕请求在连接 YouTube 时 15 秒超时；`BaW_jenozKc` 音频路径重试后同样连接超时，尚未进入媒体下载和 ffmpeg。此结果说明当前运行环境尚未完成真实视频端到端验收；不把 mock 通过视为真实网络成功。网络可用后，使用有字幕、无字幕/强制 ASR、Shorts 和已结束直播各一条验证，并检查对应 txt/metadata/SRT 和缓存复用。
