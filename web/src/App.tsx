import { useEffect } from "react";
import { TasksPage } from "./pages/TasksPage";
import { LibraryPage } from "./pages/LibraryPage";
import { Link, usePathname } from "./router";
import { HomePage } from "./pages/HomePage";
import { TaskPage } from "./pages/TaskPage";
import { VideoPage } from "./pages/VideoPage";

function route(pathname: string) {
  if (pathname === "/tasks" || pathname === "/tasks/") return <TasksPage />;
  if (pathname === "/videos" || pathname === "/videos/") return <LibraryPage />;
  const task = pathname.match(/^\/tasks\/([\w-]+)\/?$/);
  if (task) return <TaskPage key={task[1]} taskId={task[1]} />;
  const video = pathname.match(/^\/videos\/(\d+)\/?$/);
  if (video) return <VideoPage key={video[1]} videoId={Number(video[1])} />;
  if (pathname === "/" || pathname === "") return <HomePage />;
  return (
    <section className="page narrow">
      <p className="kicker">404</p>
      <h1 className="headline">这一页不存在</h1>
      <p>
        <Link href="/">回到首页</Link>
      </p>
    </section>
  );
}

export function App() {
  const pathname = usePathname();
  useEffect(() => { document.title = "video2text"; }, [pathname]);
  return (
    <div className="shell">
      <header className="masthead">
        <Link href="/" className="wordmark" aria-label="video2text 首页">
          video<span className="wordmark-two">2</span>text
        </Link>
        <nav className="main-nav" aria-label="主导航">
          {[["/", "新建转写"], ["/tasks", "任务"], ["/videos", "文字稿"]].map(([href, label]) => (
            <Link key={href} href={href} aria-current={(href === "/" ? pathname === "/" : pathname.startsWith(href)) ? "page" : undefined}>{label}</Link>
          ))}
        </nav>
      </header>
      <main>{route(pathname)}</main>
      <footer className="colophon">本地运行 · 文字稿保存在工作区 .v2t 目录</footer>
    </div>
  );
}
