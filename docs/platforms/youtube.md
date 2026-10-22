# YouTube MVP

## 当前状态（2026-10-23）

YouTube 已接入 CLI / Web，真实样本已跑通字幕获取、无字幕时下载音频并转写、缓存复用和导出。目前适合个人自用，但仍有失败情况，稳定性尚未验收通过。

最新复测中，字幕和无字幕 Shorts 转写成功；同一视频在新工作区重新下载时，两次出现 SSL EOF。测试进程额外开启模型离线模式后，也出现过模型缓存加载失败，后续同配置复测成功，原因尚未完全定位。下面同时保留成功与失败记录；自动化回归通过、真实样本跑通和逐字准确率是不同的验证范围。

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

基础依赖已含 `youtube-transcript-api`、`yt-dlp[default,deno]`；后者提供与 yt-dlp 匹配的 EJS 和 Deno（含 Docker），不需要手动安装 Node 或 Google API Key。可用 `uv run deno --version` 检查 JS 运行时。默认 ASR 的语言沿用已有配置；英文/多语视频需要 ASR 时，可把工作区 `config.json` 的 `faster_whisper.language` 设置为 `"auto"` 以自动检测。

## 网络与 Cookie

- YouTube 字幕、oEmbed 和音频默认沿用系统代理，支持 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。`V2T_USE_PROXY` 只控制 B站，不会关闭 YouTube 代理。
- yt-dlp Cookie 顺序：`V2T_COOKIE_FILE` → 工作区 `cookies.txt` → `V2T_COOKIES_FROM_BROWSER`（如 `chrome`）。字幕库不读取这些 Cookie，字幕被拦截时会回退 yt-dlp 音频路径。
- Docker 无法读取宿主机浏览器登录态，可挂载 Netscape 格式 Cookie 到 `/data/cookies.txt`。容器访问宿主机代理需要可达的宿主机地址，不能用容器自己的 `127.0.0.1`。
- 网络超时先确认本机/容器能访问 YouTube。403、bot 验证等可以先更新 `uv lock --upgrade-package yt-dlp` 并以完整 extras 组合重新 `uv sync`；Cookie 也不保证绕过所有限制，不自动配置 PO Token。

本机 HTTP 代理端口为 7897 时，可仅给当前命令配置代理（按实际端口调整）：

```bash
HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897 \
  uv run video2text tx "https://youtu.be/jNQXAC9IVRw"
HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897 \
  uv run video2text ui
```

Web 服务需要从带这些环境变量的进程启动；浏览器自身能访问 YouTube，不代表后台 Python 已配置代理。下述本机真实验证只给测试进程传入代理，没有修改系统代理或主工作区配置。使用代理也不保证每次下载成功。

实现依据：[youtube-transcript-api 官方接口](https://github.com/jdepoix/youtube-transcript-api)、[yt-dlp EJS 与运行时说明](https://github.com/yt-dlp/yt-dlp/wiki/EJS)。

## 遇到失败时

- **字幕请求失败**：程序会自动尝试音频缓存或下载音频后 ASR；最终失败时按任务日志中的阶段判断原因。
- **下载阶段出现 SSL EOF / 超时**：先确认后台进程的代理可用，再重试。2026-10-23 两次新下载在 yt-dlp 重试后仍以 SSL EOF 失败；目前没有足够证据归因到代理、YouTube 风控或某个库版本，也没有验证过确定有效的修复。已有 WAV 的任务可以跳过下载，但缓存转写成功不代表新下载问题已解决。
- **转写阶段提示找不到模型缓存快照**：先检查测试或启动环境是否设置了 `HF_HUB_OFFLINE=1`。这是 HuggingFace 的模型离线选项，项目不要求开启；缓存不完整或无法定位时，允许联网获取模型或配置经过验证的本地模型路径。此次离线缓存失败后同配置又能运行，根因仍待排查，不能仅凭缓存目录存在就判断模型可用。
- **转写语言不符合预期**：英文/多语音频使用 `faster_whisper.language="auto"`；默认中文配置会影响 ASR，字幕路径则保留原始字幕语言。链路成功不代表逐字准确，重要内容仍需核对。

## 测试与验收

### 自动化回归

自动化 mock 外部请求和识别依赖，不访问 YouTube；它验证程序逻辑，不能证明当前网络、平台接口或本机模型可用：

```bash
uv run pytest tests/test_youtube_inputs.py tests/test_youtube_downloader.py tests/test_youtube_integration.py -q
uv run pytest -q
(cd web && npm run build)
V2T_BROWSER_TEST_URL=http://127.0.0.1:5174 uv run pytest tests/test_web_browser.py -q
```

专项覆盖链接规范化/拒绝、字幕排序和时间戳、空字幕与网络异常回退、音频下载与元数据、Cookie/代理、中断、未完成直播、缓存隔离/复用、Web API 入库/SRT/重转写及浏览器提交/平台标识/复制/时间戳链接。

2026-10-23 合并代码后的自动化结果：`uv run pytest -q` 为 **258 passed / 21 skipped / 3 warnings**，21 个浏览器测试因未设置 `V2T_BROWSER_TEST_URL` 跳过；`npm run build` 通过。本轮没有另行运行浏览器回归。

2026-10-17 历史自动化结果：完整 pytest 为 229 passed / 20 skipped；另行运行浏览器回归为 20 passed。前端构建、`uv lock --check` 与 `uv run deno --version` 均通过。保留 3 条基线已有的第三方弃用警告。

### 2026-10-23 真实 CLI 复测

测试代码为 `9eaf8a5`，使用独立工作区、本机 HTTP 代理 `127.0.0.1:7897`、faster-whisper `large-v3-turbo`（CPU int8，语言 `auto`），没有使用网络或 ASR mock。依赖版本为 yt-dlp `2026.8.19`、yt-dlp-ejs `0.8.0`、Deno `2.9.7`、youtube-transcript-api `1.2.4`、faster-whisper `1.2.1`。测试进程额外设置 `HF_HUB_OFFLINE=1`，限制模型获取，不限制 YouTube 网络请求；该设置不是常规运行要求。

| 路径与样本 | 实际结果 | 耗时 |
| --- | --- | --- |
| `jNQXAC9IVRw`（Me at the zoo）字幕优先 | 成功获取 6 段英文字幕，没有下载媒体或执行 ASR；入库并导出 txt/plain/md/srt | 5.95 秒 |
| 同视频首次强制 ASR | 下载约 246 KiB 音频、ffmpeg 抽取成功；模型离线缓存加载失败，任务未完成 | 11.94 秒 |
| 同视频立即复用缓存转写 | 跳过下载和抽音频，仍在模型离线缓存加载时失败 | 0.83 秒 |
| `BGQWPY4IigY`（Shorts）普通转写 | 字幕不可用后，自动完成真实下载、ffmpeg 与 ASR；识别为英文，得到 3 段并入库 | 24.53 秒 |
| `jNQXAC9IVRw` 新工作区重新下载，两次尝试 | 两次都在下载阶段以 `[SSL: UNEXPECTED_EOF_WHILE_READING]` 失败，内部重试耗尽；未进入 ASR，也未生成可复用的完整 WAV | 43.25 / 44.31 秒 |
| 同视频使用首轮已缓存的 WAV，连续两次强制 ASR | 同配置下均成功得到 5 段英文转写，分别保存新稿并导出 txt/plain/md/srt；WAV 哈希与修改时间保持不变，进度中没有下载和抽音频阶段 | 15.20 / 14.07 秒 |

每次转写由独立 CLI 进程运行，ASR 成功轮次的耗时包含模型加载；不能直接作为同进程复用模型的性能指标。本轮覆盖真实 CLI 链路，没有操作浏览器页面，也没有复测直播、长视频或 Docker。

**本轮确认样本可跑通，尚未通过稳定性与逐字准确率验收。** 下载 SSL EOF 与模型离线缓存加载失败的根因尚未完全定位；后续成功结果不覆盖此前失败。ASR 样本仍有误词（例如把字幕中的 `trunks` 识别成 `fronts`），没有做正式准确率评测。

本机原始记录位于 `.v2t/youtube-validation-20261023-p_cozk1w/`：`validation.json` 保留全部 8 次尝试的状态、耗时、失败信息和输出路径；各轮 `.log` 保留原始日志；`exports/` 保存字幕与缓存 ASR 的导出样本；`asr-recheck/` 为两次重新下载失败的独立工作区。上述文件仅保存在本机、不进入 Git，也不混入主文字稿库。表中记录保留在仓库文档中，便于没有这些本地文件的读者了解验证范围。

### 2026-10-17 历史真实验证

初次直连 YouTube 超时；用户提供本机 HTTP 代理 `127.0.0.1:7897` 后，在独立测试工作区完成以下验证，没有使用网络或 ASR mock。以下为当日样本结果，不代表 2026-10-23 已重新验证所有路径或稳定性：

- `jNQXAC9IVRw`（Me at the zoo，19 秒）：字幕路径获取英文 6 段，跳过媒体与 ASR；强制 ASR 成功下载约 246 KiB 纯音频，经真实 ffmpeg 和本机已缓存的 faster-whisper large-v3-turbo（CPU int8、英文）得到 5 段。含模型加载的首轮约 11.92 秒；再次转写约 4.14 秒，复用相同 WAV，文件修改时间不变，进度中没有下载和抽音频阶段，新稿独立保存。三轮均入库，并成功导出 txt/plain/md/srt。
- `dQw4w9WgXcQ`：英文字幕获取成功，共 61 段，标题获取成功。
- `https://www.youtube.com/shorts/BGQWPY4IigY`（14 秒）：平台禁用字幕，普通转写自动回退到真实音频下载、ffmpeg 和 ASR（语言自动检测为英文），约 15.33 秒得到 3 段；入库和四种导出成功。
- `https://www.youtube.com/live/qVv6vCqciTM`：解析为已结束直播（`was_live`，4438 秒），获取日语字幕 1389 段。此样本只验证元数据与字幕，没有下载整场直播或运行长音频 ASR。

本机验收文件保存在独立的 `.v2t/youtube-validation-1877qdvj/`，`validation.json` 记录耗时、片段数、文件路径和缓存检查，`exports/` 保存导出样本；测试数据不进入 Git，也不混入主文字稿库。真实样本验证的是链路与输出完整性，未做人工逐字准确率评测。
