import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { getConfig, submitSources, type AppConfig } from "../api";
import { PROVIDER_LABELS } from "../format";
import { navigate } from "../router";


export function HomePage() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [source, setSource] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [showOptions, setShowOptions] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getConfig()
      .then((loaded) => {
        setConfig(loaded);
        setProvider(loaded.default_provider);
        setModel(loaded.default_model);
      })
      .catch((err: Error) => setError(`无法连接后端：${err.message}`));
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

    </section>
  );
}
