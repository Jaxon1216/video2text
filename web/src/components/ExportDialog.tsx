import { useEffect, useRef } from "react";
import { exportUrl, type ExportFormat } from "../api";

export function ExportDialog({ videoId, title, format, onClose }: {
  videoId: number;
  title: string;
  format: ExportFormat;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = ref.current!;
    dialog.showModal();
    return () => { dialog.close(); trigger?.focus(); };
  }, []);
  return (
    <dialog ref={ref} className="export-dialog" aria-labelledby="export-title" aria-describedby="export-description" onCancel={onClose}>
      <p className="kicker">保存一份文字稿</p>
      <h2 id="export-title">确认下载</h2>
      <p className="export-name">{title}</p>
      <p id="export-description" className="muted">格式：{format === "md" ? "Markdown" : format.toUpperCase()}。确认后将下载到浏览器的下载目录。</p>
      <div className="actions">
        <button type="button" className="secondary" autoFocus onClick={onClose}>取消</button>
        <a className="primary" href={exportUrl(videoId, format)} onClick={onClose}>确认下载</a>
      </div>
    </dialog>
  );
}
