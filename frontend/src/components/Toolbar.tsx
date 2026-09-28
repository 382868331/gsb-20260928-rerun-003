import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import type { RenderStatus } from "../types";

export function Toolbar() {
  const { project, assets, addAsset, setProject, addTrack } = useStudio();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(null);
  const [job, setJob] = useState<RenderStatus | null>(null);

  const flash = (kind: "ok" | "warn" | "err", text: string) => {
    setMessage({ kind, text });
    window.setTimeout(() => setMessage(null), 8000);
  };

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files?.length) return;
    setBusy("导入素材…");
    try {
      for (const file of Array.from(files)) {
        const meta = await api.uploadAsset(file, project.sample_rate);
        addAsset(meta);
      }
      flash("ok", `已导入 ${files.length} 个素材，全部统一到 ${project.sample_rate} Hz`);
    } catch (err) {
      flash("err", `导入失败：${(err as Error).message}`);
    } finally {
      setBusy(null);
      e.target.value = "";
    }
  };

  const save = async () => {
    setBusy("保存工程…");
    try {
      await api.saveProject(project);
      flash("ok", "工程已保存（只保存引用与裁切参数，素材不变）");
    } catch (err) {
      flash("err", `保存失败：${(err as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const reopen = async () => {
    setBusy("打开工程…");
    try {
      const p = await api.loadProject();
      setProject(p);
      const metas = await api.listAssets();
      metas.forEach(addAsset);
      flash("ok", `已打开工程「${p.name}」，共 ${p.tracks.length} 轨`);
    } catch (err) {
      flash("err", `打开失败：${(err as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const exportMix = async () => {
    if (!project.tracks.some((t) => t.clips.length)) {
      flash("err", "工程为空，请先放置素材");
      return;
    }
    setBusy("混音导出…");
    setJob(null);
    try {
      const started = await api.startRender(project);
      let current = started;
      while (current.status === "running") {
        await new Promise((r) => setTimeout(r, 150));
        current = await api.renderStatus(started.id);
        setJob(current);
      }
      setJob(current);
      if (current.status === "done") {
        const r = current.result!;
        flash(r.clipped ? "warn" : "ok", r.clipped ? r.warnings.join("；") : `导出完成：${r.duration.toFixed(2)}s，峰值 ${r.peak.toFixed(3)}（未削波）`);
      } else if (current.status === "cancelled") {
        flash("warn", "导出已取消，没有留下成品文件");
      } else {
        flash("err", `导出失败：${current.error}`);
      }
    } catch (err) {
      flash("err", `导出失败：${(err as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const cancelExport = async () => {
    if (job) await api.cancelRender(job.id);
  };

  return (
    <div className="toolbar">
      <input
        className="project-name"
        value={project.name}
        onChange={(e) => useStudio.setState((s) => ({ project: { ...s.project, name: e.target.value } }))}
      />
      <label className="btn">
        导入素材
        <input type="file" accept="audio/*" multiple hidden onChange={onUpload} />
      </label>
      <button onClick={addTrack}>新增轨道</button>
      <button onClick={save} disabled={!!busy}>保存工程</button>
      <button onClick={reopen} disabled={!!busy}>打开工程</button>
      <button className="primary" onClick={exportMix} disabled={!!busy}>
        混音导出
      </button>
      {job?.status === "running" && (
        <span className="exporting">
          导出中 {Math.round(job.progress * 100)}%
          <button className="danger" onClick={cancelExport} data-testid="cancel-export">
            取消导出
          </button>
        </span>
      )}
      {job?.status === "done" && (
        <a className="btn" href={api.downloadUrl(job.id)} download>
          下载成品 WAV
        </a>
      )}
      {busy && <span className="busy">{busy}</span>}
      {message && <span className={`msg ${message.kind}`} data-testid="message">{message.text}</span>}
      <span className="asset-count">素材库：{Object.keys(assets).length}</span>
    </div>
  );
}
