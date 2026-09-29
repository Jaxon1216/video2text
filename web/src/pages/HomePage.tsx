import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { getConfig, getModels, submitSources, type AppConfig, type ProviderModels } from "../api";
import { PROVIDER_LABELS } from "../format";
import { navigate } from "../router";

const CACHE_LABELS = { found: "文件已找到", missing: "未下载", incomplete: "文件不完整", unknown: "无法判断", not_applicable: "云端" };

export function HomePage() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [catalog, setCatalog] = useState<ProviderModels[]>([]);
  const [reload, setReload] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [source, setSource] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [showOptions, setShowOptions] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    Promise.all([getConfig(), getModels()])
      .then(([loaded, models]) => {
        if (cancelled) return;
        setConfig(loaded);
        setCatalog(models.items);
        setProvider(loaded.default_provider);
        setModel(loaded.default_model);
        setError(null);
      })
      .catch((err: Error) => { if (!cancelled) setError(`无法加载识别配置：${err.message}`); });
    return () => { cancelled = true; };
  }, [reload]);

  const providers = config?.providers ?? [];
  const group = catalog.find(item => item.provider === provider);
  const models = group?.models ?? [];
  const selectedModel = models.find(item => item.id === model);
  const missingSenseVoice = provider === "sensevoice" && (!model || selectedModel?.cache_status === "missing" || selectedModel?.cache_status === "incomplete");
  const canSubmit = Boolean(config && model && source.trim() && !missingSenseVoice);

  const refreshModels = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      const result = await getModels(true);
      if (mounted.current) setCatalog(result.items);
    } catch (err) {
      if (mounted.current) setRefreshError(`刷新失败，保留上次扫描结果：${(err as Error).message}`);
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (submitting || !canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const ids = await submitSources(source, { provider, model, prompt });
      setSource("");
      if (ids.length === 1) navigate(`/tasks/${ids[0]}`);
      else navigate("/tasks");
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
      <h1 className="headline">新建转写</h1>
      <p className="notice">粘贴视频链接，留下一份可以继续提问的文字。</p>
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
          <button type="submit" className="primary" disabled={submitting || !canSubmit}>
            {submitting ? "提交中…" : "转成文字"}
            <kbd>⌘↵</kbd>
          </button>
        </div>
        {showOptions && (
          <div className="options">
            <label>
              <span>识别引擎</span>
              <select value={provider} onChange={(event) => {
                const selected = event.target.value;
                setProvider(selected);
                setModel(catalog.find(item => item.provider === selected)?.default_model ?? "");
              }}>
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
              <select value={model} onChange={(event) => setModel(event.target.value)} disabled={models.length === 0}>
                {models.length === 0 && <option value="">尚未配置模型</option>}
                {models.map(item => <option key={item.id} value={item.id}>{item.label}{provider === "faster-whisper" && item.id === "large-v3-turbo" ? "（推荐）" : ""}{item.cache_status ? ` · ${CACHE_LABELS[item.cache_status]}` : ""}</option>)}
              </select>
            </label>
            <div className="wide model-status">
              <button type="button" className="link-button" disabled={refreshing} onClick={refreshModels}>{refreshing ? "正在扫描本地文件…" : "刷新本地模型状态"}</button>
              <p className="muted">{provider === "volcengine" ? "云端模型无需本地文件。" : "扫描只检查文件，实际识别时才加载模型。"}</p>
              {selectedModel?.cache_message && <p className="muted">{selectedModel.cache_message}</p>}
              {group?.dependency_installed === false && <p className="notice">引擎依赖未安装，可运行 video2text bootstrap 配置。</p>}
              {refreshError && <p className="error" role="status">{refreshError}</p>}
            </div>
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
        {missingSenseVoice && <p className="notice">请先配置 SenseVoice 本地模型目录，补齐模型文件后刷新状态。</p>}
        {selectedModel?.downloadable && selectedModel.cache_status === "missing" && <p className="notice model-warning">首次识别需要下载模型，耗时取决于网络；直接使用平台字幕时无需下载。</p>}
        {selectedModel?.downloadable && selectedModel.cache_status === "incomplete" && <p className="notice model-warning">模型文件不完整，识别时可能需要补充下载；若仍失败，请检查缓存文件。</p>}
        {error && <p className="error">{error}</p>}
        {!config && error && <button type="button" className="link-button" onClick={() => setReload(value => value + 1)}>重新加载配置</button>}
      </form>

    </section>
  );
}
