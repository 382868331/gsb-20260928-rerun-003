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
        处理顺序：源文件（重采样到工程采样率）→ 素材裁切/素材增益 → 轨道效果器链（预留）→ 轨道推子 + 静音/独听 → 母线叠加 → 主控增益 → 峰值检测 / WAV 导出。
        拖动与裁切只改工程参数，不修改素材本身。
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
