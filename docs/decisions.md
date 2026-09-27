# 决策记录

每条记录：背景 / 决定 / 原因。新决策追加在末尾。

## D1 纯自用开发，不回馈 upstream

- 背景：项目 fork 自 [lanbinleo/bili2text](https://github.com/lanbinleo/bili2text)（MIT）。
- 决定：不再给 upstream 提 PR，`main` 是自己的主线；`upstream` remote 只作参考。
- 原因：自用需求（抖音、Copy for AI、React 前端）和原项目定位不同，兼顾 PR 会显著增加拆分成本。

## D2 抖音解析用 Playwright 浏览器截获

- 背景：2026-09 实测，抖音短链与分享页返回 WAF 人机校验页，纯 HTTP 拿不到数据；yt-dlp 抖音提取器长期损坏（yt-dlp#9667）；分享页 `_ROUTER_DATA` 已不含视频信息。真实浏览器可自动通过校验，页面会请求 `/aweme/v1/web/aweme/detail/` 并加载独立音频流；音频地址带 `Referer` 可脱离浏览器下载。
- 决定：用 Playwright 打开视频页，截获详情接口 JSON 拿元数据和音频地址，再用普通 HTTP 下载。不逆向 `a_bogus` 签名。
- 原因：单条公开视频、低频、自用；不依赖签名算法和 Cookie 池，抖音改算法时最不容易坏。代价是每条多几秒、需要安装浏览器。若以后嫌慢，可引入 jiji262/douyin-downloader（MIT）的签名实现作为快速路径。

## D3 保持 SQLite + 线程池

- 决定：不引入 Redis、消息队列、Postgres。
- 原因：本地单用户，任务量小；现有 SQLite + ThreadPoolExecutor 已能提供任务、进度和历史。

## D4 全面改名

- 决定：包名 `b2t` -> `v2t`，命令 `bili2text` -> `video2text`，工作目录 `.b2t` -> `.v2t`，环境变量 `B2T_*` -> `V2T_*`。旧 `.b2t` 数据作为测试数据丢弃，不做迁移。
- 原因：不再同步 upstream，没有保留原名的必要。LICENSE 按 MIT 要求保留原作者版权声明。

## D5 Web 前端改为 React SPA

- 决定：`web/` 下 Vite + React + TypeScript，构建产物由 FastAPI 托管（单进程）。SPA 跑通后删除 Jinja 页面。
- 原因：现有 JSON API 已比较完整；SPA 更方便做 Copy for AI、时间戳跳转等交互。

## D6 默认本地引擎改为 faster-whisper

- 背景：openai-whisper 依赖 torch、体积大；中文输出常为繁体且无标点；每个任务都会重新加载模型。
- 决定：新增 faster-whisper（CTranslate2，CPU 用 int8、开启 VAD）并设为默认；`language=zh` 时自动加简体中文提示词，用户 prompt 追加在后；factory 按配置缓存 transcriber；任务并发默认 1（`V2T_TASK_WORKERS`）。
- 原因：不需要 torch，Docker 镜像更小；实测 5 分钟抖音视频输出简体带标点，加术语 prompt 后专业词识别正确。
