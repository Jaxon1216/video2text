import type { ReactNode } from "react";
import type { ProviderModels } from "../api";
import { engineNotes, modelOptionLabel, providerLabel } from "../format";

export function EngineSelects({
  catalog,
  provider,
  model,
  onChange,
  children,
}: {
  catalog: ProviderModels[];
  provider: string;
  model: string;
  onChange: (provider: string, model: string) => void;
  children?: ReactNode;
}) {
  const group = catalog.find(item => item.provider === provider);
  const models = group?.models ?? [];
  const selected = models.find(item => item.id === model);
  const notes = engineNotes(group, selected);
  return (
    <>
      <label>
        <span>识别引擎</span>
        <select value={provider} onChange={(event) => {
          const next = event.target.value;
          onChange(next, catalog.find(item => item.provider === next)?.default_model ?? "");
        }}>
          {catalog.map(item => (
            <option key={item.provider} value={item.provider}>{providerLabel(item.provider)}</option>
          ))}
        </select>
      </label>
      <label>
        <span>模型</span>
        <select value={model} onChange={(event) => onChange(provider, event.target.value)} disabled={models.length === 0}>
          {models.length === 0 && <option value="">尚未配置模型</option>}
          {models.map(item => <option key={item.id} value={item.id}>{modelOptionLabel(provider, item)}</option>)}
        </select>
      </label>
      {(notes.length > 0 || children) && (
        <div className="wide model-status">
          {children}
          {notes.map(note => <p key={note.text} className={note.tone}>{note.text}</p>)}
        </div>
      )}
    </>
  );
}
