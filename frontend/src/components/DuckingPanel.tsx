import { useState } from "react";
import { api, defaultDucking } from "../api";
import { useStudio } from "../store";
import type { PreviewReport } from "../types";

type Mode = "off" | "a" | "b";
type Reports = Partial<Record<Mode, PreviewReport>>;

function peakDb(report: PreviewReport | undefined) {
  if (!report) return "—";
  return (20 * Math.log10(Math.max(report.peak, 1e-9))).toFixed(1) + " dBFS";
}

function RangeChips({ title, ranges, danger }: {
  title: string;
  ranges: { start: number; end: number }[];
  danger?: boolean;
}) {
  if (ranges.length === 0) return null;
  return (
    <div className={danger ? "duck-danger" : "duck-ranges"}>
      <span>{title}</span>
      {ranges.slice(0, 10).map((range, i) => (
        <span key={i}>{range.start.toFixed(2)}–{range.end.toFixed(2)}s</span>
      ))}
      {ranges.length > 10 && <span>另有 {ranges.length - 10} 段</span>}
    </div>
  );
}

export function DuckingPanel() {
  const { project, ensureDucking, updateDucking, updateDuckingPreset, toggleDuckingTarget } = useStudio();
  const [reports, setReports] = useState<Reports>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const ducking = project.ducking ?? defaultDucking();
  const active = ducking.active_preset_id;
  const activePreset = ducking.presets.find((item) => item.id === active);
  const controlTrack = project.tracks.find((track) => track.id === ducking.control_track_id);

  async function analyze(mode: Mode) {
    ensureDucking();
    setBusy(true);
    setMessage("正在检测……");
    try {
      const current = useStudio.getState().project.ducking ?? defaultDucking();
      const nextProject = {
        ...useStudio.getState().project,
        ducking: { ...current, active_preset_id: mode },
      };
      await api.saveProject(nextProject);
      const report = await api.preview(nextProject);
      setReports((old) => ({ ...old, [mode]: report }));
      const label = mode === "off" ? "原始混音" : mode === "a" ? "设置 A" : "设置 B";
      setMessage(label + "检测完成");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ducking-panel">
      <div className="ducking-title">
        <strong>主持人声控制背景音乐压低</strong>
        <span>控制轨静音后仍参与控制；“原始混音”不做闪避，用于保留和比较原版本。</span>
      </div>
      <div className="ducking-grid">
        <label>控制轨
          <select
            value={ducking.control_track_id ?? ""}
            onChange={(event) => updateDucking({ control_track_id: event.target.value || null })}
            onFocus={ensureDucking}
          >
            <option value="">未选择</option>
            {project.tracks.map((track) => (
              <option key={track.id} value={track.id}>
                {track.name}{track.muted ? "（静音但仍控制）" : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="duck-targets">
          <span>背景音乐轨</span>
          {project.tracks.length === 0 && <em>先添加轨道</em>}
          {project.tracks.map((track) => (
            <label key={track.id}>
              <input
                type="checkbox"
                checked={ducking.target_track_ids.includes(track.id)}
                onChange={() => { ensureDucking(); toggleDuckingTarget(track.id); }}
              />
              {track.name}
            </label>
          ))}
        </div>
        <div className="duck-modes">
          {([
            ["off", "原始"],
            ["a", "设置 A"],
            ["b", "设置 B"],
          ] as [Mode, string][]).map(([mode, label]) => (
            <label key={mode} className={active === mode ? "duck-mode-active" : ""}>
              <input
                type="radio"
                name="ducking-mode"
                checked={active === mode}
                onChange={() => updateDucking({ active_preset_id: mode })}
                onClick={ensureDucking}
              />
              {label}
              <button type="button" disabled={busy} onClick={() => analyze(mode)}>检测</button>
            </label>
          ))}
        </div>
      </div>

      {activePreset && (
        <div className="ducking-params">
          <label>触发阈值 {activePreset.threshold_db.toFixed(0)} dB
            <input type="range" min="-60" max="0" step="1" value={activePreset.threshold_db}
              onChange={(e) => updateDuckingPreset(activePreset.id, { threshold_db: Number(e.target.value) })} />
          </label>
          <label>压低深度 {activePreset.depth_db.toFixed(0)} dB
            <input type="range" min="-40" max="-1" step="1" value={activePreset.depth_db}
              onChange={(e) => updateDuckingPreset(activePreset.id, { depth_db: Number(e.target.value) })} />
          </label>
          <label>进入 {activePreset.attack_sec.toFixed(2)}s
            <input type="number" min="0" max="10" step="0.01" value={activePreset.attack_sec}
              onChange={(e) => updateDuckingPreset(activePreset.id, { attack_sec: Number(e.target.value) })} />
          </label>
          <label>保持 {activePreset.hold_sec.toFixed(2)}s
            <input type="number" min="0" max="10" step="0.01" value={activePreset.hold_sec}
              onChange={(e) => updateDuckingPreset(activePreset.id, { hold_sec: Number(e.target.value) })} />
          </label>
          <label>恢复 {activePreset.release_sec.toFixed(2)}s
            <input type="number" min="0" max="30" step="0.01" value={activePreset.release_sec}
              onChange={(e) => updateDuckingPreset(activePreset.id, { release_sec: Number(e.target.value) })} />
          </label>
        </div>
      )}

      {controlTrack?.muted && <div className="duck-note">控制轨当前静音：不会出现在混音里，但仍会触发压低。</div>}
      <div className="duck-compare">
        {(["off", "a", "b"] as Mode[]).map((mode) => {
          const report = reports[mode];
          return (
            <div key={mode} className={"duck-report " + (report?.over_range ? "duck-report-bad" : "")}>
              <strong>{mode === "off" ? "原始" : mode === "a" ? "设置 A" : "设置 B"}</strong>
              <span>峰值 {peakDb(report)}</span>
              <span>{report ? report.over_range ? "超出范围" : "未超范围" : "未检测"}</span>
            </div>
          );
        })}
      </div>
      {(["a", "b"] as Mode[]).map((mode) => (
        <RangeChips key={mode}
          title={(mode === "a" ? "设置 A" : "设置 B") + "压低时段："}
          ranges={reports[mode]?.ducking_ranges ?? []} />
      ))}
      {(["off", "a", "b"] as Mode[]).map((mode) => (
        <RangeChips key={"over-" + mode} danger
          title={(mode === "off" ? "原始" : mode === "a" ? "设置 A" : "设置 B") + "超范围："}
          ranges={reports[mode]?.over_ranges ?? []} />
      ))}
      {message && <div className="duck-message">{message}</div>}
    </section>
  );
}
