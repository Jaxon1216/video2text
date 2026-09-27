# 路线图

状态：`待办` / `进行中` / `完成`。阶段 0~5 完成即 V1（tag `v0.1.0`）。

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 文档地基：AGENTS.md、architecture、roadmap、decisions | 完成 |
| 1 | 抖音解析：分享文本识别、Playwright 截获、下载音频、测试与文档 | 完成 |
| 2 | 减法与全面改名：删 Tk 窗口 / archive / 原作者素材；b2t -> v2t，bili2text -> video2text | 完成 |
| 3 | 时间戳 segments 与导出（带时间戳 TXT / Markdown / SRT，导出 API，CLI `--format`） | 完成 |
| 4 | faster-whisper 引擎 + transcriber 缓存 + 任务并发可配置 | 完成 |
| 5 | React SPA（Copy for AI），跑通后删除 Jinja 页面 | 完成 |
| 6 | B站字幕优先（拿不到再 ASR）+ b23.tv 短链 | 完成 |
| 7 | ASR 评测脚本：CER、术语召回、RTF、成本 | 完成（工具就绪，正式样本待准备） |
| 8 | Docker（可选） | 待办 |

## 已知问题

- openai-whisper 转写中文时经常输出繁体、没有标点；已改用 faster-whisper 为默认引擎，并在 `language=zh` 时自动加简体中文提示词。openai-whisper 保持原样。
- 专业术语（如“线程池”）容易识别错，通过 `--prompt` 传入术语可明显改善；阶段 7 评测时量化。

## V1 验收流程

粘贴 B站 / 抖音链接 → 看到处理进度 → 拿到带时间戳的文字稿 → 点 Copy for AI → 粘贴给 ChatGPT / Claude 继续提问。
