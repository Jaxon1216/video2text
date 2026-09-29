import { ExportDialog } from "../components/ExportDialog";
import { useEffect, useMemo, useRef, useState } from "react";
import { getConfig, getDocument, getModels, retranscribeVideo, type ExportFormat, type ProviderModels, type TranscriptDocument } from "../api";
import {
  PLATFORM_LABELS,
  PROVIDER_LABELS,
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

export function VideoPage({ videoId }: { videoId: number }) {
  const [document, setDocument] = useState<TranscriptDocument | null>(null);
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
    getDocument(videoId)
      .then((loaded) => {
        setDocument(loaded);
        window.document.title = `${loaded.title} · video2text`;
      })
      .catch((err: Error) => setError(err.message));
    return () => {
      window.document.title = "video2text";
    };
  }, [videoId]);

  const blocks = useMemo(() => (document?.has_timestamps ? groupSegments(document.segments) : []), [document]);
  const withHours = needsHours(blocks);

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
      setCatalog(models.items);
      setProvider(selected);
      setModel(selected === document?.engine ? document.model : models.items.find(item => item.provider === selected)?.default_model ?? "");
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

  if (error) {
    return (
      <section className="page narrow">
        <p className="kicker">文字稿</p>
        <h1 className="headline">打不开这份文字稿</h1>
        <p className="error">{error}</p>
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
          <span>{characters.toLocaleString("zh-CN")} 字</span>
        </p>
        <h1 className="headline">{document.title}</h1>
        <p className="muted byline">
          {sourceDescription(document)}
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
          <label><span>识别引擎</span><select value={provider} onChange={event => {
            const selected = event.target.value;
            setProvider(selected);
            setModel(catalog.find(item => item.provider === selected)?.default_model ?? "");
          }}>{catalog.map(item => <option key={item.provider} value={item.provider}>{PROVIDER_LABELS[item.provider] ?? item.provider}{item.enabled ? "" : "（未配置）"}</option>)}</select></label>
          <label><span>模型</span><select value={model} onChange={event => setModel(event.target.value)}>{(catalog.find(item => item.provider === provider)?.models ?? []).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
          <label className="wide"><span>术语提示</span><input value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="可选：视频里的专业词" /></label>
          <p className="muted wide">这次会用所选模型重新识别。已有完整音频时直接复用，否则需要重新下载；新文字稿会单独保存。</p>
          <div className="wide"><button className="primary" disabled={!model || retranscribing} onClick={startRetranscribe}>{retranscribing ? "提交中…" : "开始重转写"}</button></div>
        </section>
      )}

      {exportFormat && <ExportDialog videoId={document.video_id} title={document.title} format={exportFormat} onClose={() => setExportFormat(null)} />}

      {!document.has_timestamps && (
        <p className="notice">
          {document.version_kind === "edited" ? "当前是编辑过的版本，时间戳已不再对应，只显示纯文本。" : "这个识别引擎没有提供时间戳，只显示纯文本。"}
        </p>
      )}

      <div className="transcript">
        {document.has_timestamps ? (
          blocks.map((block, index) => {
            const stamp = formatTimestamp(block.start, withHours);
            const link = timeLink(document, block.start);
            return (
              <section key={block.start} className="passage" style={{ animationDelay: `${Math.min(index, 16) * 25}ms` }}>
                {link ? (
                  <a className="stamp" href={link} target="_blank" rel="noreferrer" title="在 B站 从这里播放">
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

      <div className={`toast ${toast ? "is-visible" : ""}`} role="status" aria-live="polite">
        {toast}
      </div>
    </article>
  );
}
