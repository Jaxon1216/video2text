import { useEffect, useState } from "react";
import { cancelTask, listTasks, type Task } from "../api";
import { formatDate, stageLabel } from "../format";
import { Link } from "../router";

export function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const cancel = async (taskId: string) => {
    setCancelling(taskId);
    try {
      const updated = await cancelTask(taskId);
      setTasks(current => current?.map(task => task.id === taskId ? updated : task) ?? null);
      setError(null);
    } catch (err) {
      setError(`中断失败：${(err as Error).message}`);
    } finally {
      setCancelling(null);
    }
  };
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const items = await listTasks();
        if (cancelled) return;
        setTasks(items);
        setError(null);
        timer = window.setTimeout(poll, items.some(t => ["queued", "running"].includes(t.status)) ? 1500 : 6000);
      } catch (err) {
        if (!cancelled) {
          setError(`连接中断，正在重试：${(err as Error).message}`);
          timer = window.setTimeout(poll, 6000);
        }
      }
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, []);
  return (
    <section className="page">
      <h1 className="headline">任务</h1>
      <p className="notice">查看处理进度，也可以回到失败的任务重试。</p>
      {error && <p className="error" role="status">{error}</p>}
      {tasks === null ? (!error && <p className="muted">加载中…</p>) : tasks.length === 0 ? <p className="empty muted">还没有任务。<Link href="/">新建转写</Link></p> : (
        <ul className="task-list">
          {tasks.map(task => (
            <li key={task.id}>
              <Link href={`/tasks/${task.id}`} className="task-row">
                <span className="task-source">{task.source_input}<small className="task-meta">{formatDate(task.created_at)} · {task.provider} {task.model}</small></span>
                <span className="task-stage">{task.status === "failed" ? "处理失败" : task.status === "completed" ? "已完成" : task.status === "cancelled" ? "已取消" : task.cancel_requested ? "正在中断" : stageLabel(task.current_stage)}</span>
                <span className="task-percent">{Math.round(task.progress_percent * 100)}%</span>
              </Link>
              {["queued", "running"].includes(task.status) && !task.cancel_requested && (
                <button className="chip" onClick={() => cancel(task.id)} disabled={cancelling === task.id}>中断任务</button>
              )}
              {task.status === "failed" && <p className="task-error">{task.error_message}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
