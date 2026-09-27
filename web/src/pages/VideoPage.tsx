import { useEffect, useMemo, useRef, useState } from "react";
import { exportUrl, getDocument, type TranscriptDocument } from "../api";
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
import { Link } from "../router";

export function VideoPage({ videoId }: { videoId: number }) {
  const [document, setDocument] = useState<TranscriptDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
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

  if (error) {
    return (
      <section className="page narrow">
        <p className="kicker">文字稿</p>
        <h1 className="headline">打不开这份文字稿</h1>
        <p className="error">{error}</p>
        <Link href="/">回到首页</Link>
      </section>
    );
  }
  if (!document) return <section className="page narrow muted">加载中…</section>;

  const duration = formatDuration(document.duration);
  const characters = document.text.replace(/\s/g, "").length;

  return (
    <article className="page reader">
      <Link href="/" className="back">
        ← 全部文字稿
      </Link>
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
        <span className="toolbar-gap" />
        <span className="toolbar-label">下载</span>
        <a className="chip" href={exportUrl(document.video_id, "txt")}>
          TXT
        </a>
        <a className="chip" href={exportUrl(document.video_id, "md")}>
          Markdown
        </a>
        {document.has_timestamps ? (
          <a className="chip" href={exportUrl(document.video_id, "srt")}>
            SRT
          </a>
        ) : (
          <span className="chip is-disabled" title="没有时间戳，无法导出字幕">
            SRT
          </span>
        )}
      </div>

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
