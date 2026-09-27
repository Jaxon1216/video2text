# ASR 评测

用来回答：中文技术视频上，免费本地 ASR 和付费云 ASR 哪个更准、术语识别怎么样、速度差多少、成本差多少。

## 准备样本

在仓库根目录建 `bench/`（已被 `.gitignore` 忽略），每个样本一组同名文件：

```text
bench/
  threadpool.m4a            音频或视频片段，3~5 分钟为宜
  threadpool.ref.txt        人工校对过的参考文字稿（必需）
  threadpool.terms.txt      可选：专业术语，一行一个
  threadpool.subtitle.srt   可选：平台字幕（例如 B站 AI 字幕），作为对照组
```

建议 3~5 段，覆盖：普通话讲解、中英混杂术语（React、Kubernetes、useEffect）、快语速或有背景音乐。

从已转写的视频里截片段：

```bash
ffmpeg -ss 00:01:00 -t 240 -i .v2t/downloads/douyin-xxx.m4a bench/threadpool.m4a
```

参考稿可以先用 `video2text export <id> --format plain` 导出，再对照音频逐句修正（这是最花时间的一步，但决定了结果是否可信）。参考稿必须和片段的起止完全对应，多出或缺少的句子都会被算成错误。

## 运行

```bash
export HF_ENDPOINT=https://hf-mirror.com   # 国内下载本地模型
uv run python scripts/bench_asr.py bench/ \
  --engine faster-whisper:small \
  --engine faster-whisper:large-v3-turbo \
  --engine whisper:small \
  --engine volcengine \
  --prompt-variants \
  --price volcengine=<元/小时>
```

- `--engine provider[:model]` 可重复；可用 provider：`faster-whisper`、`whisper`、`sensevoice`、`volcengine`。
- 云引擎的 API Key 从工作区 `config.json` 读取（`video2text bootstrap` 配置，或 `--workspace` 指定别的工作区）。
- `--prompt-variants`：每个引擎再跑一遍“把术语作为提示词”的版本。
- `--price`：云服务每小时音频的单价，用于估算成本；以官方当时的价格为准。本地引擎成本记为 0。
- 报告输出到 `bench/reports/<时间>/report.md`，同目录保存每个引擎的识别原文和 `results.json`。

## 指标

| 指标 | 含义 |
| --- | --- |
| CER | 字错误率 = 编辑距离 / 参考稿字数。先做 NFKC（全角转半角）、转小写、去掉标点和空白。**不做繁简转换**，繁体输出会被算错 |
| 术语召回 | 术语表里有多少个词原样出现在识别结果中（忽略大小写和全半角） |
| RTF | 识别耗时 / 音频时长，不含模型加载（单独列出），越低越快 |
| 成本 | 单价 × 音频时长 |

CER 和术语召回要一起看：提示词常常把术语改对，同时也可能把人名等别的词带偏，CER 不一定下降。

## 冒烟测试记录（2026-09，仅用于验证脚本）

45 秒抖音片段（雅思介绍，参考稿为人工修正），faster-whisper small，Mac CPU：

| 变体 | CER | 术语召回 | RTF |
| --- | --- | --- | --- |
| 无提示词 | 6.5% | 2/5 | 0.48 |
| 术语提示词 | 6.5% | 5/5 | 0.45 |

样本太少，不能作为结论；正式对比请按上面的步骤准备 3~5 段样本。
