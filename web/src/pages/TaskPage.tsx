import { useEffect, useState } from "react";
import { ApiError, getTask, submitSources, type Task } from "../api";
import { PIPELINE_STAGES, messageLabel, stageLabel } from "../format";
import { Link, navigate } from "../router";

export function TaskPage({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const latest = await getTask(taskId);
        if (cancelled) return;
        setTask(latest);
        setError(null);
        if (latest.status === "completed" && latest.video_id !== null) {
          navigate(`/videos/${latest.video_id}`, { replace: true, state: { transcriptionCompleted: true } });
          return;
        }
        if (latest.status === "running" || latest.status === "queued") timer = window.setTimeout(poll, 1000);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setMissing(true);
        } else {
          setError(`连接中断，正在重试：${(err as Error).message}`);
          timer = window.setTimeout(poll, 6000);
        }
      }
    };
    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [taskId]);

  const retry = async () => {
    if (!task) return;
    setRetrying(true);
    try {
      const [id] = await submitSources(task.source_input, { provider: task.provider, model: task.model, prompt: "" });
      navigate(`/tasks/${id}`, { replace: true });
    } catch (err) {
      setError((err as Error).message);
      setRetrying(false);
    }
  };

  if (missing) {
    return (
      <section className="page narrow">
        <p className="kicker">任务</p>
        <h1 className="headline">找不到这个任务</h1>
        <p className="error">任务不存在或已被移除。</p>
        <Link href="/tasks">返回任务列表</Link>
      </section>
    );
  }

  const percent = Math.round((task?.progress_percent ?? 0) * 100);
  const failed = task?.status === "failed";
  const terminal = task && ["completed", "failed", "cancelled"].includes(task.status);
  const currentIndex = task ? PIPELINE_STAGES.indexOf(task.current_stage as (typeof PIPELINE_STAGES)[number]) : -1;
  const detail = task ? messageLabel(task.current_message) : null;

  return (
    <section className="page narrow">
      <Link href="/tasks" className="back">← 全部任务</Link>
      <p className="kicker">{failed ? "处理失败" : task?.status === "cancelled" ? "已取消" : task?.status === "completed" ? "已完成" : task?.status === "queued" ? "排队中" : "正在处理"}</p>
      {error && <p className="error" role="status">{error}</p>}
      <h1 className="headline source-line">{task?.source_input ?? "…"}</h1>
      <p className="muted">
        {task ? `${task.provider} ${task.model}` : "加载中"}
        {detail && !failed ? ` · ${detail}` : ""}
      </p>

      <div className={`progress ${terminal ? "is-failed" : ""}`}>
        <div className="progress-bar" style={{ width: `${failed ? 100 : Math.max(percent, 3)}%` }} />
        <span className="progress-value">{failed ? "×" : `${percent}%`}</span>
      </div>

      <ol className="stages">
        {PIPELINE_STAGES.map((stage, index) => {
          const state =
            task?.status === "completed" || index < currentIndex
              ? "done"
              : index === currentIndex && !terminal
                ? failed
                  ? "failed"
                  : "current"
                : "todo";
          return (
            <li key={stage} className={`stage stage-${state}`}>
              <span className="stage-dot" />
              {stageLabel(stage)}
            </li>
          );
        })}
      </ol>

      {failed && (
        <div className="failure">
          <p className="error">{task?.error_message || "未知错误"}</p>
          <div className="actions">
            <button className="primary" onClick={retry} disabled={retrying}>
              {retrying ? "重新提交中…" : "重试"}
            </button>
            <Link href="/tasks" className="secondary">
              返回任务列表
            </Link>
          </div>
        </div>
      )}
    </section>
  );
}
