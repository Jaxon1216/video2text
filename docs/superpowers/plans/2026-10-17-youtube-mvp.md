# YouTube MVP 实施计划

采用用户附件推荐的 `youtube-transcript-api → yt-dlp bestaudio → 现有 ASR` 方案，直接在本次会话完成开发和验证。

## 范围与约束

- 单视频：watch、youtu.be、Shorts、live 录播、embed；规范化为 watch URL，忽略播放列表和起播时间。
- 只在 inputs 解析文本，不访问网络；校验域名和 11 位视频 ID。
- 人工中文字幕（zh-Hans、zh-CN、zh 等）→ 人工英文 → 自动中文 → 自动英文 → 其他原始字幕；不自动翻译。
- 字幕时间转换为秒级 start/end；字幕不可用由现有 Pipeline 回退 ASR，强制 ASR 跳过字幕。
- 纯音频优先、单视频下载；拒绝正在直播/预约视频；复用 Cookie、进度与取消处理，YouTube 默认使用系统代理。
- 音频按平台和视频 ID 缓存；不在 Pipeline 添加平台特判，不重构 B站实现。
- 自动化测试全部 mock 网络。真实链接只做独立手动冒烟，明确记录实际结果。
- 保留已有 web/src/format.ts、TasksPage.tsx、styles.css 用户修改。

## 执行清单

- [x] 输入与缓存：先用参数化测试覆盖链接、非法输入、跨 URL 缓存及工厂注册；观察失败，再加入 youtube 来源类型、解析和缓存支持。
- [x] 下载器：先用 mock API/yt-dlp 测试字幕优先级、空字幕、异常回退、音频路径、元数据、代理/Cookie、直播限制和中断；再实现 youtube.py 及必要的公共 yt-dlp 辅助函数。
- [x] 集成：通过真实 Pipeline + mock 外部依赖验证字幕不调用 ASR、无字幕和强制 ASR、缓存重用、入库/导出/重转写；补 Web 浏览器链接提交和平台展示验证。
- [x] 文案与文档：更新 CLI / Web 提示、平台标识和原链接复制，构建前端；同步 AGENTS、README、architecture、roadmap、decisions、api 及平台文档。
- [x] 验证与交付：完整 pytest、浏览器检查、前端构建、差异审查和手动网络冒烟；只提交本功能修改并按项目约定合回 main。

## 验证命令

```bash
source ~/.zshrc
uv run pytest tests/test_inputs.py tests/test_audio_cache.py tests/test_youtube_downloader.py tests/test_youtube_integration.py -q
uv run pytest -q
(cd web && npm run build)
V2T_BROWSER_TEST_URL=http://127.0.0.1:5174 uv run pytest tests/test_web_browser.py -q
git diff --check
```

基线：185 passed、19 skipped（可选浏览器测试），3 条第三方弃用警告。

验收结果：完整 pytest 229 passed / 20 skipped；显式浏览器回归 20 passed；前端构建、锁文件与 Deno 检查通过。独立审查无阻塞问题，导出平台名小项已用失败/通过回归修正。真实网络冒烟连接 YouTube 超时，尚未完成真实视频端到端验收。
