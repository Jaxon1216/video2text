import { useEffect, useRef, useState } from "react";
import { ApiError, getBilibiliCatalog, type BilibiliCatalog } from "../api";
import { formatTimestamp } from "../format";

function catalogSource(text: string): string | null {
  const source = text.trim();
  if (source.includes("\n")) return null;
  if (/^BV[A-Za-z0-9]{10}$/.test(source)) return source;
  const match = source.match(/https?:\/\/[^\s\u3000-\u303f\uff00-\uffef"'<>]+/i);
  if (!match) return null;
  try {
    const url = new URL(match[0].replace(/[.,;:!?)\]]+$/, ""));
    if (/(^|\.)(b23\.tv|bili2233\.cn)$/.test(url.hostname)) return url.href;
    if (/(^|\.)bilibili\.com$/.test(url.hostname) && /BV[A-Za-z0-9]{10}/.test(url.pathname)) return url.href;
  } catch {
    return null;
  }
  return null;
}

type Lookup = { source: string; catalog: BilibiliCatalog } | { source: string; error: string; invalidSource: boolean };

export function useBilibiliPicker(text: string) {
  const source = catalogSource(text);
  const [lookup, setLookup] = useState<Lookup | null>(null);
  const [selection, setSelection] = useState<string[] | null>(null);
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setLookup(null);
    setSelection(null);
    setOpen(false);
    if (!source) return;
    let stale = false;
    const timer = window.setTimeout(async () => {
      try {
        const catalog = await getBilibiliCatalog(source);
        if (stale) return;
        setLookup({ source, catalog });
        setOpen(catalog.kind !== "single");
      } catch (err) {
        if (!stale) setLookup({ source, error: (err as Error).message, invalidSource: err instanceof ApiError && err.status === 400 });
      }
    }, 300);
    return () => { stale = true; window.clearTimeout(timer); };
  }, [source, attempt]);

  const current = lookup?.source === source ? lookup : null;
  const catalog = current && "catalog" in current && current.catalog.kind !== "single" ? current.catalog : null;
  return {
    pending: !!source && !current, catalog,
    error: current && "error" in current ? current.error : null,
    invalidSource: current && "error" in current ? current.invalidSource : false,
    retry: () => setAttempt(value => value + 1),
    open, setOpen, selection: catalog ? selection : null,
    confirm: (sources: string[]) => { setSelection(sources); setOpen(false); },
  };
}

export function BilibiliPicker({ catalog, selection, onConfirm, onCancel, includeScreenshots, onScreenshotsChange,
  onSubmit, submitting, canSubmit, error }: {
  catalog: BilibiliCatalog;
  selection: string[] | null;
  onConfirm: (sources: string[]) => void;
  onCancel: () => void;
  includeScreenshots: boolean;
  onScreenshotsChange: (value: boolean) => void;
  onSubmit: (sources: string[]) => void;
  submitting: boolean;
  canSubmit: boolean;
  error: string | null;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [checked, setChecked] = useState(() => new Set(selection ?? catalog.items.filter(item => item.selected).map(item => item.source)));
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current!;
    dialog.showModal();
    dialog.querySelector<HTMLInputElement>(".catalog-items input:checked")?.focus();
    return () => { dialog.close(); trigger?.focus(); };
  }, []);
  const selectAll = checked.size === catalog.items.length;
  const sources = catalog.items.filter(item => checked.has(item.source)).map(item => item.source);
  const totalDuration = catalog.items.every(item => item.duration !== null)
    ? catalog.items.reduce((sum, item) => sum + item.duration!, 0) : null;

  return (
    <dialog className="catalog-dialog" ref={dialogRef} aria-label="选择转写集数"
      onCancel={event => { event.preventDefault(); if (!submitting) onCancel(); }}>
      <header>
        <p className="kicker">{catalog.kind === "parts" ? "B 站分 P" : "B 站合集"}</p>
        <h2>{catalog.title}</h2>
        <div className="catalog-controls">
          <span>共 {catalog.items.length} 集{totalDuration !== null ? ` · ${formatTimestamp(totalDuration)}` : ""}</span>
          <button type="button" className="link-button" disabled={submitting} onClick={() => {
            setChecked(selectAll ? new Set() : new Set(catalog.items.map(item => item.source)));
          }}>{selectAll ? "清空选择" : "全选"}</button>
        </div>
      </header>
      <div className="catalog-items" role="group" aria-label="选集列表">
        {catalog.items.map((item, index) => (
          <label key={item.source}>
            <input type="checkbox" checked={checked.has(item.source)} disabled={submitting} onChange={event => {
              const next = new Set(checked);
              if (event.target.checked) next.add(item.source); else next.delete(item.source);
              setChecked(next);
            }} />
            <span className="catalog-index">{catalog.kind === "parts"
              ? `P${new URL(item.source, window.location.origin).searchParams.get("p") ?? index + 1}` : index + 1}</span>
            <span className="catalog-title">{item.title}</span>
            <span className="catalog-duration">{item.duration !== null ? formatTimestamp(item.duration) : "时长未知"}</span>
          </label>
        ))}
      </div>
      <footer>
        <div className="catalog-controls">
          <label><input type="checkbox" checked={includeScreenshots} disabled={submitting}
            onChange={event => onScreenshotsChange(event.target.checked)} /> 为文字稿配图</label>
          <span aria-live="polite">已选 {checked.size} 集</span>
        </div>
        {error && <p className="error" role="status">{error}</p>}
        <div className="catalog-actions">
          <button type="button" className="link-button" disabled={submitting} onClick={onCancel}>取消</button>
          <button type="button" className="secondary" disabled={!sources.length || submitting}
            onClick={() => onConfirm(sources)}>返回设置</button>
          <button type="button" className="primary" disabled={!sources.length || !canSubmit || submitting}
            onClick={() => onSubmit(sources)}>{submitting ? "提交中…" : `转写所选 ${sources.length} 集`}</button>
        </div>
      </footer>
    </dialog>
  );
}
