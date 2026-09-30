import { useEffect, useRef, useState } from "react";
import { api, type ExportStatus } from "../api";
import { useStudio } from "../store";
import type { MediaInfo, PreviewReport } from "../types";

export function Toolbar() {
  const { project, addClip, rememberMedia, mediaDurations, setMasterGain } = useStudio();
  const [message, setMessage] = useState("就绪");
  const [preview, setPreview] = useState<PreviewReport | null>(null);
  const [exportStatus, setExportStatus] = useState<ExportStatus | null>(null);
  const [forceExport, setForceExport] = useState(false);
  const [rangeStart, setRangeStart] = useState("");
  const [rangeEnd, setRangeEnd] = useState("");
  const pollRef = useRef<number | null>(null);

  async function importMedia() {
    const path = window.prompt("输入服务器上可访问的音频文件绝对路径（wav/flac/ogg 等）：");
    if (!path) return;
    try {
      const info: MediaInfo = await api.importMedia(path);
      rememberMedia(info.source, info.duration);
      setMessage(`已导入 ${info.original_name}（${info.sample_rate} Hz，将统一到工程 ${project.sample_rate} Hz）`);
      if (project.tracks.length === 0) useStudio.getState().addTrack();
      const trackId = useStudio.getState().project.tracks[0].id;
      addClip(trackId, info.source, info.duration);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function checkMix() {
    try {
      await api.saveProject(useStudio.getState().project);
      const report = await api.preview(useStudio.getState().project);
      setPreview(report);
      setMessage(report.over_range
        ? `叠加超出输出范围：${report.over_count} 个采样点，请降低轨道/主控增益`
        : `峰值 ${(20 * Math.log10(Math.max(report.peak, 1e-9))).toFixed(1)} dBFS，未超出范围`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function saveProject() {
    try {
      await api.saveProject(project);
      await api.persist();
      setMessage("工程已保存，重新打开可还原同样的声音");
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  function stopPolling() {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  async function startExport() {
    const output = window.prompt("导出文件路径（.wav）：", "mix-export.wav");
    if (!output) return;
    try {
      await api.saveProject(useStudio.getState().project);
      const range = {
        ...(rangeStart.trim() !== "" ? { start_time: Number(rangeStart) } : {}),
        ...(rangeEnd.trim() !== "" ? { end_time: Number(rangeEnd) } : {}),
      };
      const { job_id } = await api.startExport(output, forceExport, range);
      setExportStatus(null);
      setMessage("导出中……可随时取消，取消不会留下文件");
      stopPolling();
      pollRef.current = window.setInterval(async () => {
        const status = await api.exportStatus(job_id);
        setExportStatus(status);
        if (status.status !== "running") {
          stopPolling();
          setMessage(status.status === "done"
            ? `导出完成：${status.output_path}`
            : status.status === "cancelled" ? "导出已取消，未生成成品文件" : `导出失败：${status.message}`);
        }
      }, 250);
    } catch (error) {
      const err = error as Error & { body?: { over_range?: boolean; over_count?: number; over_ranges?: { start: number; end: number }[] } };
      if (err.body?.over_range) {
        const ranges = (err.body.over_ranges ?? []).slice(0, 5)
          .map((r) => `${r.start.toFixed(2)}-${r.end.toFixed(2)}s`).join("，");
        setMessage(`导出被阻止：叠加超出输出范围（${err.body.over_count} 点；区间：${ranges}）。请降低增益，或勾选强制导出`);
      } else {
        setMessage(err.message);
      }
    }
  }

  async function cancelExport() {
    if (!exportStatus) return;
    await api.cancelExport(exportStatus.id);
    setMessage("正在取消……");
  }

  useEffect(() => stopPolling, []);

  const running = exportStatus?.status === "running";

  return (
    <div className="toolbar">
      <div className="toolbar-row">
        <strong className="app-title">声音后期制作工作室</strong>
        <span className="project-meta">{project.sample_rate} Hz · {project.channels === 2 ? "立体声" : "单声道"}</span>
        <button onClick={importMedia}>导入素材</button>
        <button onClick={() => useStudio.getState().addTrack()}>添加轨道</button>
        <button onClick={saveProject}>保存工程</button>
        <button onClick={checkMix}>检测混音</button>
        <label className="force-export">
          <input type="checkbox" checked={forceExport} onChange={(e) => setForceExport(e.target.checked)} />
          超范围仍导出
        </label>
        <label className="range-field">
          分块导出 起
          <input value={rangeStart} placeholder="0" onChange={(e) => setRangeStart(e.target.value)} />
          止
          <input value={rangeEnd} placeholder="结尾" onChange={(e) => setRangeEnd(e.target.value)} />
        </label>
        {running ? (
          <button className="btn-danger" onClick={cancelExport}>取消导出</button>
        ) : (
          <button className="btn-primary" onClick={startExport}>混音导出</button>
        )}
        <label className="master-gain">
          主控 {project.master_gain_db.toFixed(1)} dB
          <input type="range" min={-24} max={6} step={0.5} value={project.master_gain_db}
            onChange={(e) => setMasterGain(Number(e.target.value))} />
        </label>
      </div>
      <div className="toolbar-row status-row">
        <span className={`status ${preview?.over_range ? "status-error" : ""}`}>{message}</span>
        {running && (
          <progress value={exportStatus?.progress ?? 0} max={1} />
        )}
        {preview?.over_range && (
          <span className="over-ranges">
            超范围区间：
            {preview.over_ranges.slice(0, 8).map((r, i) => (
              <span key={i} className="over-range-chip">{r.start.toFixed(2)}–{r.end.toFixed(2)}s</span>
            ))}
          </span>
        )}
      </div>
      {Object.keys(mediaDurations).length === 0 && (
        <div className="toolbar-row hint">先点击“导入素材”，把音频文件复制进工程 media 目录（源文件不会被修改）。</div>
      )}
    </div>
  );
}
