import { Link, usePathname } from "./router";
import { HomePage } from "./pages/HomePage";
import { TaskPage } from "./pages/TaskPage";
import { VideoPage } from "./pages/VideoPage";

function route(pathname: string) {
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
  return (
    <div className="shell">
      <header className="masthead">
        <Link href="/" className="wordmark" aria-label="video2text 首页">
          video<span className="wordmark-two">2</span>text
        </Link>
        <span className="masthead-note">把视频变成可以提问的文字</span>
      </header>
      <main>{route(pathname)}</main>
      <footer className="colophon">本地运行 · 文字稿保存在工作区 .v2t 目录</footer>
    </div>
  );
}
