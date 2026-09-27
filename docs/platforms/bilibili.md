# B站

## 支持范围

- 输入：BV 号、`bilibili.com/video/BV...` 链接、App 里复制的分享文本（只要里面有带 BV 号的链接）。
- 分 P：链接里的 `?p=N` 会只下载第 N P。
- 暂不支持：`b23.tv` 短链（里面没有 BV 号，计划在阶段 6 支持）、番剧 / 课程等特殊页面。

## 原理

`downloaders/ytdlp.py` 调用 yt-dlp 下载（格式 `bv*+ba/b`，合并为 mp4），再由 pipeline 用 ffmpeg 抽出 16k wav 做 ASR。

## 配置

| 环境变量 | 默认 | 作用 |
| --- | --- | --- |
| `B2T_COOKIE_FILE` | `<工作区>/cookies.txt` | Netscape 格式的 cookies.txt，遇到 HTTP 412 或需要登录时使用 |
| `B2T_USE_PROXY` | 关 | B站 CDN 经常屏蔽代理节点，默认直连；设为 `1` 走系统代理 |

## 常见问题

- HTTP 412：B站风控。先升级 yt-dlp（`uv lock --upgrade-package yt-dlp && uv sync ...`），仍不行就导出浏览器 cookies.txt。

## 计划

- 阶段 6：优先使用 B站 CC / AI 字幕（需要登录 cookie），拿不到再走 ASR；支持 `b23.tv` 短链。
