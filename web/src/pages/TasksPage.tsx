import { useEffect, useState } from "react";
import { listTasks, type Task } from "../api";
import { formatDate, stageLabel } from "../format";
import { Link } from "../router";

export function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const items = await listTasks();
        if (cancelled) return;
        setTasks(items);
        timer = window.setTimeout(poll, items.some(t => ["queued", "running"].includes(t.status)) ? 1500 : 6000);
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, 6000);
      }
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, []);
  return (
    <section className="page">
      <h1 className="headline">任务</h1>
      <p className="notice">查看处理进度，也可以回到失败的任务重试。</p>
      {tasks === null ? <p className="muted">加载中…</p> : tasks.length === 0 ? <p className="empty muted">还没有任务。<Link href="/">新建转写</Link></p> : (
        <ul className="task-list">
          {tasks.map(task => (
            <li key={task.id}>
              <Link href={`/tasks/${task.id}`} className="task-row">
                <span className="task-source">{task.source_input}<small className="task-meta">{formatDate(task.created_at)} · {task.provider} {task.model}</small></span>
                <span className="task-stage">{task.status === "failed" ? "处理失败" : task.status === "completed" ? "已完成" : task.status === "cancelled" ? "已取消" : stageLabel(task.current_stage)}</span>
                <span className="task-percent">{Math.round(task.progress_percent * 100)}%</span>
              </Link>
              {task.status === "failed" && <p className="task-error">{task.error_message}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
