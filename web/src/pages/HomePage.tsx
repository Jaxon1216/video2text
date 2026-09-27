import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { getConfig, listTasks, listVideos, submitSources, type AppConfig, type Task, type VideoItem } from "../api";
import { PLATFORM_LABELS, PROVIDER_LABELS, formatDate, stageLabel } from "../format";
import { Link, navigate } from "../router";

const ACTIVE: Task["status"][] = ["queued", "running"];

export function HomePage() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [source, setSource] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [showOptions, setShowOptions] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [videos, setVideos] = useState<VideoItem[] | null>(null);
  const [query, setQuery] = useState("");
  const queryRef = useRef(query);
  queryRef.current = query;

  useEffect(() => {
    getConfig()
      .then((loaded) => {
        setConfig(loaded);
        setProvider(loaded.default_provider);
        setModel(loaded.default_model);
      })
      .catch((err: Error) => setError(`无法连接后端：${err.message}`));
  }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      listVideos(query).then(setVideos).catch(() => setVideos([]));
    }, 200);
    return () => window.clearTimeout(handle);
  }, [query]);

  const activeTasks = useMemo(() => tasks.filter((task) => ACTIVE.includes(task.status)), [tasks]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const all = await listTasks();
        if (cancelled) return;
        setTasks(all);
        const stillActive = all.some((task) => ACTIVE.includes(task.status));
        if (!stillActive) listVideos(queryRef.current).then(setVideos).catch(() => undefined);
        timer = window.setTimeout(poll, stillActive ? 1500 : 6000);
      } catch {
        timer = window.setTimeout(poll, 6000);
      }
    };
    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const providers = config?.providers ?? [];

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const ids = await submitSources(source, { provider, model, prompt });
      setSource("");
      if (ids.length === 1) navigate(`/tasks/${ids[0]}`);
      else setTasks(await listTasks());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) submit();
  };

  return (
    <section className="page">
      <form className="intake" onSubmit={submit}>
        <label className="kicker" htmlFor="source">
          粘贴链接
        </label>
        <textarea
          id="source"
          className="intake-box"
          value={source}
          onChange={(event) => setSource(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={"B站 / 抖音链接，或 App 里复制的整段分享文本\n多条链接可以一行一个"}
          rows={4}
          autoFocus
        />
        <div className="intake-row">
          <button type="button" className="link-button" onClick={() => setShowOptions((value) => !value)}>
            {showOptions ? "收起选项" : "识别选项"}
            <span className="muted">
              {" "}
              · {provider || "…"} {model}
            </span>
          </button>
          <button type="submit" className="primary" disabled={submitting || !source.trim()}>
            {submitting ? "提交中…" : "转成文字"}
            <kbd>⌘↵</kbd>
          </button>
        </div>
        {showOptions && (
          <div className="options">
            <label>
              <span>识别引擎</span>
              <select value={provider} onChange={(event) => setProvider(event.target.value)}>
                {providers.map((name) => (
                  <option key={name} value={name}>
                    {PROVIDER_LABELS[name] ?? name}
                    {config?.enabled_providers.includes(name) ? "" : "（未配置）"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>模型</span>
              <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="small / medium / large-v3-turbo" />
            </label>
            <label className="wide">
              <span>术语提示（可选）</span>
              <input
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="例如：线程池、核心线程、阻塞队列。写上视频里的专业词，识别会准很多"
              />
            </label>
          </div>
        )}
        {error && <p className="error">{error}</p>}
      </form>

      {activeTasks.length > 0 && (
        <section className="section">
          <h2 className="section-title">处理中</h2>
          <ul className="task-list">
            {activeTasks.map((task) => (
              <li key={task.id}>
                <Link href={`/tasks/${task.id}`} className="task-row">
                  <span className="task-source">{task.source_input}</span>
                  <span className="task-stage">{stageLabel(task.current_stage)}</span>
                  <span className="meter" aria-hidden>
                    <span style={{ width: `${Math.round(task.progress_percent * 100)}%` }} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">文字稿</h2>
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题" />
        </div>
        {videos === null ? (
          <p className="muted">加载中…</p>
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
              </li>
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}
