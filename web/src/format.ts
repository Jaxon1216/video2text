import type { ModelOption, ProviderModels, Segment, TranscriptDocument } from "./api";

export const BLOCK_SECONDS = 30;

export const PLATFORM_LABELS: Record<string, string> = {
  bilibili: "B站",
  douyin: "抖音",
  video: "本地视频",
  audio: "本地音频",
};

export const PROVIDER_LABELS: Record<string, string> = {
  "faster-whisper": "faster-whisper",
  whisper: "Whisper",
  "qwen3-asr": "Qwen3-ASR",
  sensevoice: "SenseVoice",
  volcengine: "火山引擎",
};

const CACHE_LABELS: Record<ModelOption["cache_status"], string> = {
  found: "文件已找到",
  missing: "未下载",
  incomplete: "文件不完整",
  unknown: "无法判断",
  not_applicable: "云端",
};

export function providerLabel(name: string): string {
  return PROVIDER_LABELS[name] ?? name;
}

export function modelOptionLabel(provider: string, item: ModelOption): string {
  const recommended = provider === "faster-whisper" && item.id === "large-v3-turbo" ? "（推荐）" : "";
  const cache = item.cache_status ? ` · ${CACHE_LABELS[item.cache_status]}` : "";
  return `${item.label}${recommended}${cache}`;
}

/** Keep a saved model only when it belongs to this engine; otherwise use that engine's default. */
export function initialModel(group: ProviderModels | undefined, preferred: string): string {
  if (group?.models.some(item => item.id === preferred)) return preferred;
  return group?.default_model ?? preferred;
}

export function engineNotes(group: ProviderModels | undefined, model: ModelOption | undefined): { text: string; tone: "muted" | "notice" }[] {
  if (!group) return [];
  const notes: { text: string; tone: "muted" | "notice" }[] = [];
  if (group.provider === "qwen3-asr") notes.push({ text: "本地模型，结果没有时间戳。", tone: "muted" });
  if (!group.enabled) notes.push({ text: "这个引擎尚未在配置里启用。", tone: "muted" });
  if (group.dependency_installed === false) notes.push({ text: "引擎依赖未安装，可运行 video2text bootstrap 配置。", tone: "notice" });
  notes.push({
    text: group.provider === "volcengine" ? "云端模型无需本地文件。" : "扫描只检查文件，实际识别时才加载模型。",
    tone: "muted",
  });
  if (model?.cache_message) notes.push({ text: model.cache_message, tone: "muted" });
  return notes;
}

const STAGE_LABELS: Record<string, string> = {
  queued: "排队中",
  preparing: "准备中",
  downloading: "获取视频",
  extracting_audio: "提取音频",
  transcribing: "语音识别",
  writing_outputs: "保存结果",
  indexing: "整理入库",
  completed: "完成",
  failed: "失败",
};

const MESSAGE_LABELS: Record<string, string> = {
  resolving: "正在解析视频信息",
  downloading: "正在下载",
  download_finished: "下载完成",
  loading_model: "正在加载模型",
  checking_subtitles: "正在检查平台字幕",
  subtitles_unavailable: "字幕获取失败，改用语音识别",
};

export const PIPELINE_STAGES = ["downloading", "extracting_audio", "transcribing", "writing_outputs"] as const;

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

export function messageLabel(message: string): string | null {
  return MESSAGE_LABELS[message] ?? null;
}

export function formatTimestamp(seconds: number, withHours = false): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(secs).padStart(2, "0");
  return withHours || hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatDuration(seconds: number | null): string | null {
  return seconds ? formatTimestamp(seconds) : null;
}

const CJK = /[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]/;

export function joinText(left: string, right: string): string {
  if (!left) return right;
  if (!right) return left;
  const separator = CJK.test(left.at(-1) ?? "") || CJK.test(right[0]) ? "" : " ";
  return `${left}${separator}${right}`;
}

/** Same grouping as the backend exporter, so what you read is what you download. */
export function groupSegments(segments: Segment[], blockSeconds = BLOCK_SECONDS): Segment[] {
  const blocks: Segment[] = [];
  for (const segment of segments) {
    const current = blocks.at(-1);
    if (current && segment.start - current.start < blockSeconds) {
      current.end = segment.end;
      current.text = joinText(current.text, segment.text);
    } else {
      blocks.push({ ...segment });
    }
  }
  return blocks;
}

export function needsHours(blocks: Segment[]): boolean {
  return (blocks.at(-1)?.end ?? 0) >= 3600;
}

/** Only Bilibili honours a start-time query parameter on its web player. */
export function timeLink(document: TranscriptDocument, seconds: number): string | null {
  if (document.platform !== "bilibili" || !document.url) return null;
  try {
    const url = new URL(document.url);
    url.searchParams.set("t", String(Math.floor(seconds)));
    return url.toString();
  } catch {
    return null;
  }
}

export function transcriptBody(document: TranscriptDocument): string {
  if (!document.has_timestamps) return document.text.trim();
  const blocks = groupSegments(document.segments);
  const withHours = needsHours(blocks);
  return blocks.map((block) => `[${formatTimestamp(block.start, withHours)}] ${block.text}`).join("\n\n");
}

export function sourceDescription(document: TranscriptDocument): string {
  if (document.transcript_source === "subtitle") {
    return document.model.startsWith("ai-") ? "平台 AI 字幕" : "平台字幕";
  }
  return `自动语音识别（${[document.engine, document.model].filter(Boolean).join(" ")}）`;
}

function isHumanSubtitle(document: TranscriptDocument): boolean {
  return document.transcript_source === "subtitle" && !document.model.startsWith("ai-");
}

export function buildCopyForAI(document: TranscriptDocument): string {
  const info = [
    `标题：${document.title}`,
    [
      `平台：${PLATFORM_LABELS[document.platform] ?? document.platform}`,
      document.uploader ? `作者：${document.uploader}` : null,
      formatDuration(document.duration) ? `时长：${formatDuration(document.duration)}` : null,
    ]
      .filter(Boolean)
      .join(" | "),
    document.url ? `链接：${document.url}` : null,
    `文字稿来源：${sourceDescription(document)}${
      isHumanSubtitle(document) ? "" : "，可能有错别字、同音字或专业术语识别错误，请结合上下文理解"
    }`,
  ].filter(Boolean);

  const intro = document.has_timestamps
    ? "下面是一个视频的文字稿，每段开头的 [mm:ss] 是该段在视频中的时间点。请基于这份文字稿回答我接下来的问题；引用内容时请注明对应的时间点。"
    : "下面是一个视频的文字稿。请基于这份文字稿回答我接下来的问题。";

  return [intro, "", ...info, "", "<transcript>", transcriptBody(document), "</transcript>", ""].join("\n");
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // e.g. "Document is not focused"; the legacy path below still works from a click handler
    }
  }
  // Plain-http LAN access (server mode) has no Clipboard API.
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!ok) throw new Error("浏览器不允许复制，请手动选择文字");
}
