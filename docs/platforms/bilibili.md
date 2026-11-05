# B站

B站的媒体和字幕都通过 yt-dlp 获取。短链展开、字幕优先和 Cookie 在下载器里完成，机制说明见 `docs/architecture.md` 的「远程媒体怎么拿到」。

## 支持范围

- 输入：BV 号、`bilibili.com/video/BV...` 链接、`b23.tv` 短链、App 里复制的整段分享文本。
- 分 P：链接里的 `?p=N` 只处理第 N P。
- 暂不支持：番剧 / 课程等特殊页面。

## 在新建页选择集数

视频链接本身的 BV 标识投稿，`?p=N` 标识当前分 P。Web 用视频信息接口 `/x/web-interface/view` 取目录。若同一 BV 含多个 `pages`，按分 P 顺序显示 `part`、时长和对应的单集链接。如果当前 BV 只含一个分 P，且返回了 `ugc_season.sections[].episodes[]`，则展示该合集的公开成员及各自的 BV。普通单集直接沿用原来的提交方式。详情接口限流或超时时，会额外查询一次 `/x/player/pagelist`；只有拿到连续且有效的多个分 P 才能据此展示选集，此时用 BV 号作总标题。单项响应无法确认是否属于跨 BV 合集。

打开目录只读取元数据。同一 BV 的成功目录在服务进程内复用 5 分钟。目录不可用时新建页提供重试和直接处理当前链接的入口；指定了无效分 P 时需要修正链接。选择集数并提交后，每集按目录顺序独立下载、转写并按需配图，保留本集时间轴；前一集失败或取消也会轮到下一集。

## 流程：字幕优先，ASR 兜底

```mermaid
flowchart LR
    Input["BV / 链接 / 短链"] --> Resolve["b23.tv 跟随跳转得到 BV"]
    Resolve --> Subs{"fetch_subtitles：有字幕？"}
    Subs -->|"有（人工字幕优先，其次 AI 字幕）"| Done["直接用字幕 segments"]
    Subs -->|"没有 / 出错"| Download["yt-dlp 下载"] --> ASR["ffmpeg + ASR"]
```

1. `YtDlpDownloader.resolve_source`：`b23.tv` / `bili2233.cn` 短链先跟随跳转，得到带 BV 号的地址（在下载器里做，输入解析阶段不访问网络）。
2. `fetch_subtitles`：`extract_info(download=False, writesubtitles=True)`，按 `zh-CN > zh-Hans > zh > 繁体 > ai-zh > en > ai-en` 选字幕，弹幕（`danmaku`）永远排除。字幕内容解析为 segments（SRT 或 B站 JSON）。
3. 拿到字幕：不下载视频、不跑 ASR，`engine=subtitle`、`model=<字幕语言>`、`transcript_source=subtitle`。前端会显示“平台字幕 / 平台 AI 字幕”。
4. 没有字幕或获取出错：照常下载并做 ASR。

Web 新建页勾选「为文字稿配图」后，正文登记完成才查找同一 BV、同一分 P 的本地媒体。已有媒体能解码出画面就直接用于采样。如果只有字幕或音频，则用当前 Cookie 和代理设置，通过 yt-dlp 补取选定分 P 的视频流，并单独保存在 `downloads/screenshot-media/`。补取失败时仍可阅读正文。

**B站字幕需要登录**：不带 Cookie 时 B站只返回弹幕，会直接回退到 ASR。

## 配置

| 环境变量 / 选项 | 默认 | 作用 |
| --- | --- | --- |
| `V2T_COOKIE_FILE` | `<工作区>/cookies.txt` | Netscape 格式 cookies.txt |
| `V2T_COOKIES_FROM_BROWSER` | 空 | 没有 cookies.txt 时，直接读浏览器登录态，例如 `chrome` / `edge` / `firefox`。macOS 首次读取 Chrome 会弹出钥匙串授权 |
| `V2T_USE_PROXY` | 关 | B站 CDN 经常屏蔽代理节点，默认直连；设为 `1` 走系统代理 |
| `config.json` 的 `prefer_subtitles` | `true` | 是否优先使用平台字幕 |
| CLI `--force-asr` | - | 本次忽略字幕，强制 ASR（评测识别效果时有用） |

## 常见问题

- HTTP 412：B站风控。先升级 yt-dlp（`uv lock --upgrade-package yt-dlp`，再按 `docs/development.md` 的组合 `uv sync`），仍不行就配置 Cookie。
- 有字幕但没用上：确认 Cookie 有效（登录状态），`video2text tx` 输出里会看到“正在检查平台字幕”。
