import { EngineSelects } from "../components/EngineSelects";
import { ExportDialog } from "../components/ExportDialog";
import { useEffect, useMemo, useRef, useState } from "react";
import { getConfig, getDocument, getModels, getTranscriptVersions, retranscribeVideo, type ExportFormat, type ProviderModels, type TranscriptDocument, type TranscriptVersions } from "../api";
import {
  PLATFORM_LABELS,
  buildCopyForAI,
  copyText,
  formatDuration,
  formatTimestamp,
  groupSegments,
  needsHours,
  sourceDescription,
  timeLink,
  transcriptBody,
} from "../format";
import { Link, navigate } from "../router";

const VERSION_TABS = [
  { kind: "original", label: "原始稿" },
  { kind: "ai_optimized", label: "AI 优化" },
  { kind: "edited", label: "编辑稿" },
];

export function VideoPage({ videoId }: { videoId: number }) {
  const [document, setDocument] = useState<TranscriptDocument | null>(null);
  const [versions, setVersions] = useState<TranscriptVersions | null>(null);
  const [versionKind, setVersionKind] = useState("original");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState<ExportFormat | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showRetranscribe, setShowRetranscribe] = useState(false);
  const [catalog, setCatalog] = useState<ProviderModels[]>([]);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [retranscribing, setRetranscribing] = useState(false);
  const [retranscribeError, setRetranscribeError] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError(null);
    setExportFormat(null);
    getTranscriptVersions(videoId)
      .then(async (loadedVersions) => {
        const selected = loadedVersions.items.find(version => version.kind === versionKind);
        const loaded = await getDocument(videoId, selected?.id);
        if (disposed) return;
        setVersions(loadedVersions);
        setDocument(loaded);
        window.document.title = `${loaded.title} · video2text`;
      })
      .catch((err: Error) => { if (!disposed) setError(err.message); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [videoId, versionKind, refresh]);

  useEffect(() => {
    return () => {
      window.document.title = "video2text";
      window.clearTimeout(toastTimer.current);
    };
  }, []);

  const blocks = useMemo(() => (document?.has_timestamps ? groupSegments(document.segments) : []), [document]);
  const withHours = needsHours(blocks);
  const tabs = VERSION_TABS.filter(tab => tab.kind !== "edited" || versions?.items.some(version => version.kind === "edited"));
  const selectedVersion = versions?.items.find(version => version.kind === versionKind);
  const available = !loading && !error && selectedVersion?.id === document?.version_id && !!selectedVersion;

  const flash = (message: string) => {
    setToast(message);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 1800);
  };

  const copy = async (text: string, message: string) => {
    try {
      await copyText(text);
      flash(message);
    } catch (err) {
      flash((err as Error).message);
    }
  };

  const openRetranscribe = async () => {
    if (showRetranscribe) { setShowRetranscribe(false); return; }
    try {
      const [config, models] = await Promise.all([getConfig(), getModels()]);
      const selected = models.items.some(item => item.provider === document?.engine) ? document!.engine : config.default_provider;
      const group = models.items.find(item => item.provider === selected);
      const previousModelAvailable = selected === document?.engine && group?.models.some(item => item.id === document.model);
      setCatalog(models.items);
      setProvider(selected);
      setModel(previousModelAvailable ? document!.model : group?.default_model ?? "");
      setRetranscribeError(null);
      setShowRetranscribe(true);
    } catch (err) {
      setRetranscribeError(`无法加载模型选项：${(err as Error).message}`);
    }
  };

  const startRetranscribe = async () => {
    if (!document || !model || retranscribing) return;
    setRetranscribing(true);
    setRetranscribeError(null);
    try {
      const created = await retranscribeVideo(document.video_id, { provider, model, prompt });
      navigate(`/tasks/${created.task_id}`);
    } catch (err) {
      setRetranscribeError(`重转写失败：${(err as Error).message}`);
      setRetranscribing(false);
    }
  };

  if (error && !document) {
    return (
      <section className="page narrow">
        <p className="kicker">文字稿</p>
        <h1 className="headline">打不开这份文字稿</h1>
        <p className="error">{error}</p>
        <button className="secondary" onClick={() => setRefresh(value => value + 1)}>重新加载</button>
        <Link href="/videos">返回文字稿列表</Link>
      </section>
    );
  }
  if (!document) return <section className="page narrow muted">加载中…</section>;

  const duration = formatDuration(document.duration);
  const characters = document.text.replace(/\s/g, "").length;

  return (
    <article className="page reader">
      <Link href="/videos" className="back">
        ← 全部文字稿
      </Link>
      {window.history.state?.transcriptionCompleted && <p className="notice" role="status">转写完成，文字稿已保存。</p>}
      <header className="reader-head">
        <p className="kicker">
          <span className={`tag tag-${document.platform}`}>{PLATFORM_LABELS[document.platform] ?? document.platform}</span>
          {document.uploader && <span>{document.uploader}</span>}
          {duration && <span>{duration}</span>}
          {available && <span>{characters.toLocaleString("zh-CN")} 字</span>}
        </p>
        <h1 className="headline">{document.title}</h1>
        <p className="muted byline">
          {available ? sourceDescription(document) : "文字稿"}
          {document.url && (
            <>
              {" · "}
              <a href={document.url} target="_blank" rel="noreferrer">
                打开原视频 ↗
              </a>
            </>
          )}
        </p>
      </header>

      <div className="version-switcher">
        <div role="tablist" aria-label="文字稿版本" onKeyDown={event => {
          const index = tabs.findIndex(tab => tab.kind === versionKind);
          const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
            : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
            : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
          if (next === null) return;
          event.preventDefault();
          setVersionKind(tabs[next].kind);
          event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next].focus();
        }}>
          {tabs.map(tab => (
            <button key={tab.kind} role="tab" id={`tab-${tab.kind}`} aria-controls="transcript-panel"
              aria-selected={versionKind === tab.kind} tabIndex={versionKind === tab.kind ? 0 : -1}
              onClick={() => setVersionKind(tab.kind)}>
              {tab.label}
            </button>
          ))}
        </div>
        <button className="chip" disabled={loading} onClick={() => setRefresh(value => value + 1)}>
          {loading ? "载入中…" : "刷新稿件"}
        </button>
      </div>
      {versions?.ai_import_error && <p className="notice" role="status">{versions.ai_import_error} 已保存的稿件仍可查看。</p>}
      {error && <p className="notice error" role="status">稿件加载失败：{error}，请点击“刷新稿件”重试。</p>}

      <div id="transcript-panel" role="tabpanel" aria-labelledby={`tab-${versionKind}`} aria-busy={loading}>
      {loading ? <p className="notice">正在载入稿件…</p> : !available ? (
        !error && <section className="ai-empty">
          <h2>还没有 AI 优化稿</h2>
          <p>切到“原始稿”，点击 Copy for AI，交给 AI 润色修复。</p>
          <p>将优化后的纯文本保存到下方路径，再点击“刷新稿件”：</p>
          <code>{versions?.ai_output_path}</code>
        </section>
      ) : <>
      <div className="toolbar" role="toolbar" aria-label="复制与导出">
        <button className="primary" onClick={() => copy(buildCopyForAI(document), "已复制，粘贴给 ChatGPT / Claude 吧")}>
          Copy for AI
        </button>
        <button className="secondary" onClick={() => copy(transcriptBody(document), "已复制全文")}>
          复制全文
        </button>
        <button className="secondary" onClick={openRetranscribe}>换模型重转写</button>
        <span className="toolbar-gap" />
        <span className="toolbar-label">下载</span>
        <button className="chip" onClick={() => setExportFormat("txt")}>TXT</button>
        <button className="chip" onClick={() => setExportFormat("md")}>Markdown</button>
        {document.has_timestamps ? (
          <button className="chip" onClick={() => setExportFormat("srt")}>SRT</button>
        ) : (
          <span className="chip is-disabled" title="没有时间戳，无法导出字幕">SRT</span>
        )}
      </div>

      {retranscribeError && <p className="error" role="status">{retranscribeError}</p>}
      {showRetranscribe && (
        <section className="options" aria-label="换模型重转写选项">
          <EngineSelects catalog={catalog} provider={provider} model={model} onChange={(nextProvider, nextModel) => {
            setProvider(nextProvider);
            setModel(nextModel);
          }} />
          <label className="wide"><span>术语提示</span><input value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="可选：视频里的专业词" /></label>
          <p className="muted wide">这次会用所选模型重新识别。已有完整音频时直接复用，否则需要重新下载；新文字稿会单独保存。</p>
          <div className="wide"><button className="primary" disabled={!model || retranscribing} onClick={startRetranscribe}>{retranscribing ? "提交中…" : "开始重转写"}</button></div>
        </section>
      )}

      {exportFormat && <ExportDialog videoId={document.video_id} versionId={document.version_id} title={document.title} format={exportFormat} onClose={() => setExportFormat(null)} />}

      {!document.has_timestamps && (
        <p className="notice">
          {document.version_kind === "ai_optimized" ? "当前是 AI 优化稿，已移除时间戳。复制和下载使用这份稿件。"
            : document.version_kind === "edited" ? "当前是编辑过的版本，时间戳已不再对应，只显示纯文本。" : "这个识别引擎没有提供时间戳，只显示纯文本。"}
        </p>
      )}

      {document.version_kind === "ai_optimized" && (
        <details className="ai-file-hint">
          <summary>更新这份优化稿</summary>
          <p>保存到此路径后，点击“刷新稿件”即可载入新版本：</p>
          <code>{versions?.ai_output_path}</code>
        </details>
      )}

      <div className="transcript">
        {document.has_timestamps ? (
          blocks.map((block, index) => {
            const stamp = formatTimestamp(block.start, withHours);
            const link = timeLink(document, block.start);
            return (
              <section key={block.start} className="passage" style={{ animationDelay: `${Math.min(index, 16) * 25}ms` }}>
                {link ? (
                  <a className="stamp" href={link} target="_blank" rel="noreferrer" title={`在 ${PLATFORM_LABELS[document.platform] ?? document.platform} 从这里播放`}>
                    {stamp}
                  </a>
                ) : (
                  <span className="stamp">{stamp}</span>
                )}
                <p>{block.text}</p>
                <button
                  className="passage-copy"
                  onClick={() => copy(`[${stamp}] ${block.text}`, `已复制 ${stamp} 这一段`)}
                  aria-label={`复制 ${stamp} 这一段`}
                >
                  复制
                </button>
              </section>
            );
          })
        ) : (
          <section className="passage plain">
            <p>{document.text}</p>
          </section>
        )}
      </div>
      </>}
      </div>

      <div className={`toast ${toast ? "is-visible" : ""}`} role="status" aria-live="polite">
        {toast}
      </div>
    </article>
  );
}
