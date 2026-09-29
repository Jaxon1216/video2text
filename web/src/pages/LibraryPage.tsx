import { useEffect, useState } from "react";
import { listVideos, type VideoItem } from "../api";
import { PLATFORM_LABELS, formatDate, copyText } from "../format";
import { Link } from "../router";

export function LibraryPage() {
  const [videos, setVideos] = useState<VideoItem[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 1800);
    return () => window.clearTimeout(timer);
  }, [toast]);
  const copyLink = async (url: string) => {
    try { await copyText(url); setToast("已复制原视频链接"); }
    catch (err) { setToast((err as Error).message); }
  };
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      listVideos(query).then((items) => {
        if (!cancelled) { setVideos(items); setError(null); }
      }).catch((err: Error) => {
        if (!cancelled) setError(`文字稿加载失败：${err.message}`);
      });
    }, 200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query, retry]);
  return (
    <section className="page">
      <h1 className="headline">文字稿</h1>
      <p className="notice">读过的视频，都在这里。</p>
      {error && <div className="error" role="status">{error} <button className="link-button" onClick={() => setRetry(value => value + 1)}>重试</button></div>}
      <section className="section">
        <div className="section-head">
          <h2 className="section-title">全部文字稿</h2>
          <input className="search" aria-label="搜索标题" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题" />
        </div>
        {videos === null ? (
          !error && <p className="muted">加载中…</p>
        ) : videos.length === 0 ? (
          <p className="muted empty">{query ? "没有匹配的文字稿" : "还没有文字稿。粘贴第一个链接试试。"}</p>
        ) : (
          <ol className="library">
            {videos.map((video, index) => (
              <li key={video.id} style={{ animationDelay: `${Math.min(index, 12) * 30}ms` }}>
                <Link href={`/videos/${video.id}`} className="library-item">
                  <span className="library-title">{video.title}</span>
                  <span className="library-meta">
                    <span className={`tag tag-${video.source_kind}`}>{PLATFORM_LABELS[video.source_kind] ?? video.source_kind}</span>
                    <span>{formatDate(video.created_at)}</span>
                    <span>{video.engine}</span>
                  </span>
                </Link>
                {video.source_url && ["bilibili", "douyin"].includes(video.source_kind) && (
                  <button className="chip source-copy" aria-label={`复制${video.title}的原视频链接`} onClick={() => copyLink(video.source_url!)}>复制链接</button>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
      <div className={`toast ${toast ? "is-visible" : ""}`} role="status" aria-live="polite">{toast}</div>
    </section>
  );
}
