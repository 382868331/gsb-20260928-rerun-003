import { useStudio } from "../store";
import type { PreviewReport } from "../types";

interface Props {
  preview: PreviewReport | null;
  onPreview: () => void;
}

const labels = { none: "原混音", A: "方案 A", B: "方案 B" } as const;

export function DuckingPanel({ preview, onPreview }: Props) {
  const {
    project,
    setActiveDucking,
    updateDuckingProfile,
  } = useStudio();
  const activeName = project.active_ducking === "none" ? null : project.active_ducking;
  const profile = activeName ? project.ducking_profiles[activeName] : null;

  return (
    <section className="ducking-panel">
      <div className="ducking-title">
        <strong>闪避控制</strong>
        <div className="ducking-modes">
          {(["none", "A", "B"] as const).map((mode) => (
            <button
              key={mode}
              className={project.active_ducking === mode ? "active" : ""}
              onClick={() => setActiveDucking(mode)}
            >
              {labels[mode]}
            </button>
          ))}
        </div>
        <button onClick={onPreview}>检测压低区间</button>
      </div>
      {profile && (
        <div className="ducking-grid">
          <label>触发 {profile.threshold_db.toFixed(0)} dB
            <input type="range" min={-60} max={-6} step={1}
              value={profile.threshold_db}
              onChange={(e) => updateDuckingProfile(activeName!, { threshold_db: Number(e.target.value) })} />
          </label>
          <label>压低 {profile.reduction_db.toFixed(0)} dB
            <input type="range" min={0} max={36} step={1}
              value={profile.reduction_db}
              onChange={(e) => updateDuckingProfile(activeName!, { reduction_db: Number(e.target.value) })} />
          </label>
          <label>进入 {profile.attack_sec.toFixed(2)}s
            <input type="range" min={0} max={0.5} step={0.01}
              value={profile.attack_sec}
              onChange={(e) => updateDuckingProfile(activeName!, { attack_sec: Number(e.target.value) })} />
          </label>
          <label>保持 {profile.hold_sec.toFixed(2)}s
            <input type="range" min={0} max={1} step={0.01}
              value={profile.hold_sec}
              onChange={(e) => updateDuckingProfile(activeName!, { hold_sec: Number(e.target.value) })} />
          </label>
          <label>恢复 {profile.release_sec.toFixed(2)}s
            <input type="range" min={0.05} max={3} step={0.05}
              value={profile.release_sec}
              onChange={(e) => updateDuckingProfile(activeName!, { release_sec: Number(e.target.value) })} />
          </label>
        </div>
      )}
      <p className="hint">把主持人轨设为“控制”、背景音乐轨设为“背景”；控制轨静音后仍参与闪避。</p>
      {preview?.duck_ranges && preview.duck_ranges.length > 0 && (
        <div className="duck-ranges">
          压低区间：
          {preview.duck_ranges.slice(0, 12).map((range, i) => (
            <span key={i}>{range.start.toFixed(2)}–{range.end.toFixed(2)}s</span>
          ))}
        </div>
      )}
    </section>
  );
}
