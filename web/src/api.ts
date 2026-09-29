export type TaskStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface AppConfig {
  default_provider: string;
  default_model: string;
  providers: string[];
  enabled_providers: string[];
}

export interface ModelOption {
  id: string;
  label: string;
}

export interface ProviderModels {
  provider: string;
  default_model: string;
  enabled: boolean;
  models: ModelOption[];
}

export function getModels(): Promise<{ items: ProviderModels[] }> {
  return request("/api/models");
}

export interface Task {
  id: string;
  status: TaskStatus;
  source_input: string;
  provider: string;
  model: string;
  progress_percent: number;
  current_stage: string;
  current_message: string;
  error_message: string;
  video_id: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface VideoItem {
  id: number;
  source_kind: string;
  source_input: string;
  source_url: string | null;
  title: string;
  engine: string;
  model: string;
  created_at: string;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
}

export interface TranscriptDocument {
  video_id: number;
  title: string;
  platform: string;
  url: string | null;
  uploader: string | null;
  duration: number | null;
  engine: string;
  model: string;
  transcript_source: string;
  version_kind: string;
  text: string;
  segments: Segment[];
  has_timestamps: boolean;
}

export type ExportFormat = "txt" | "plain" | "md" | "srt";

export interface SubmitOptions {
  provider: string;
  model: string;
  prompt: string;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(15000),
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // keep the status line
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export function getConfig(): Promise<AppConfig> {
  return request<AppConfig>("/api/config");
}

/** Submits one task per non-empty line; returns the created task ids in order. */
export async function submitSources(sourceText: string, options: SubmitOptions): Promise<string[]> {
  const lines = sourceText
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
  if (lines.length === 0) throw new Error("请先粘贴一个链接或分享文本");
  if (lines.length === 1) {
    const created = await request<{ task_id: string }>("/api/tasks/transcribe", {
      method: "POST",
      body: JSON.stringify({ source: lines[0], ...options }),
    });
    return [created.task_id];
  }
  const created = await request<{ items: Task[] }>("/api/tasks/batch", {
    method: "POST",
    body: JSON.stringify({ sources: lines, ...options }),
  });
  return created.items.map((task) => task.id);
}

export function getTask(taskId: string): Promise<Task> {
  return request<Task>(`/api/tasks/${taskId}`);
}

export async function listTasks(): Promise<Task[]> {
  return (await request<{ items: Task[] }>("/api/tasks")).items;
}

export async function listVideos(query = ""): Promise<VideoItem[]> {
  const search = query.trim() ? `?query=${encodeURIComponent(query.trim())}` : "";
  return (await request<{ items: VideoItem[] }>(`/api/videos${search}`)).items;
}

export function getDocument(videoId: number): Promise<TranscriptDocument> {
  return request<TranscriptDocument>(`/api/videos/${videoId}/document`);
}

export function exportUrl(videoId: number, format: ExportFormat): string {
  return `/api/videos/${videoId}/export?format=${format}`;
}
