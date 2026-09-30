import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { useStudio } from "./store";
import { Toolbar } from "./components/Toolbar";
import { Timeline } from "./components/Timeline";
import "./styles.css";

function App() {
  const { setProject, rememberMedia } = useStudio();
  const [error, setError] = useState("");

  useEffect(() => {
    api.loadProject()
      .then((project) => {
        setProject(project);
        const sources = new Set<string>();
        for (const track of project.tracks) {
          for (const clip of track.clips) sources.add(clip.source);
        }
        for (const source of sources) {
          api.mediaInfo(source).then((info) => rememberMedia(info.source, info.duration)).catch(() => undefined);
        }
      })
      .catch((err) => setError(err.message));
  }, [setProject, rememberMedia]);

  return (
    <div className="app">
      <Toolbar />
      {error && <div className="fatal-error">无法连接后端：{error}（请先运行 backend/run_server.py）</div>}
      <Timeline />
      <footer className="chain-doc">
        处理顺序：源文件（重采样到工程采样率）→ 素材裁切/素材内音量与声像自动化 → 轨道效果器链与效果强度自动化 → 轨道音量/声像自动化与推子 → 母线叠加 → 主控增益。
        素材自动化使用片段内相对时间并随片段移动；轨道自动化使用工程时间。静音/独听不改变导出长度。
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
