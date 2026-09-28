import { useEffect } from "react";
import { Toolbar } from "./components/Toolbar";
import { AssetBin } from "./components/AssetBin";
import { Timeline } from "./components/Timeline";
import { api } from "./api";
import { useStudio } from "./store";
import "./styles.css";

export default function App() {
  const setProject = useStudio((s) => s.setProject);
  const addAsset = useStudio((s) => s.addAsset);

  useEffect(() => {
    void (async () => {
      try {
        const [p, metas] = await Promise.all([api.loadProject(), api.listAssets()]);
        if (p.tracks.length || p.name !== "untitled" || p.asset_ids.length) setProject(p);
        metas.forEach(addAsset);
      } catch {
        // 后端未启动时保持空工程
      }
    })();
  }, [setProject, addAsset]);

  return (
    <div className="app">
      <header>
        <h1>声音后期制作工作室</h1>
        <p className="pipeline-hint">处理链：素材裁切 → 片段增益 → 轨道叠加 · 静音/独听 · 轨道增益（效果器预留位）→ 主总线削波检测 → 导出</p>
      </header>
      <Toolbar />
      <div className="workspace">
        <AssetBin />
        <Timeline />
      </div>
    </div>
  );
}
