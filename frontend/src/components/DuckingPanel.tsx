import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import type { DuckingConfig, DuckingConfigV2, DuckingReport, DuckingTargetReport, PreviewReport } from "../types";

function isV2(ducking: DuckingConfig | DuckingConfigV2 | null | undefined): ducking is DuckingConfigV2 {
  return !!ducking && (ducking as DuckingConfigV2).version === 2;
}

function peakDb(report: PreviewReport | undefined) {
  if (!report) return "—";
  return (20 * Math.log10(Math.max(report.peak, 1e-9))).toFixed(1) + " dBFS";
}

function sourceLabel(range: DuckingTargetReport["ranges"][number], report: DuckingReport) {
  if (!range.control_ids?.length) return range.control ?? "—";
  const currentRoles = useStudio.getState().project.ducking as DuckingConfigV2;
  const activePlan = currentRoles.plans.find((item) => item.id === report.plan_id);
  const names = range.control_ids.map((id) =>
    activePlan?.roles.find((role) => role.id === id)?.name ?? "已删除角色");
  return names.join("+");
}

function LegacyPanel() {
  const { project, ensureDucking, updateDucking, updateDuckingPreset, toggleDuckingTarget } = useStudio();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [legacyReport, setLegacyReport] = useState<PreviewReport | null>(null);
  const ducking = project.ducking as DuckingConfig | null;
  if (!ducking) {
    return (
      <section className="ducking-panel">
        <div className="ducking-title">
          <strong>多角色闪避编排</strong>
          <span>把主持人、远端嘉宾、提示音等控制音轨归为角色，分别安排压低程度与优先级。</span>
        </div>
        <button type="button" onClick={ensureDucking}>创建多角色闪避方案</button>
      </section>
    );
  }
  const activePreset = ducking.presets.find((item) => item.id === ducking.active_preset_id);

  async function analyze() {
    setBusy(true);
    setMessage("正在检测……");
    try {
      const report = await api.preview(useStudio.getState().project);
      setLegacyReport(report);
      setMessage("检测完成：峰值 " + peakDb(report));
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ducking-panel">
      <div className="ducking-title">
        <strong>侧链闪避（旧版单控制轨）</strong>
        <span>可继续检测；升级为多角色后原方案仍保留在工程历史中用于对照。</span>
      </div>
      <div className="ducking-grid">
        <label>控制轨
          <select
            value={ducking.control_track_id ?? ""}
            onChange={(event) => updateDucking({ control_track_id: event.target.value || null })}
          >
            <option value="">未选择</option>
            {project.tracks.map((track) => (
              <option key={track.id} value={track.id}>{track.name}</option>
            ))}
          </select>
        </label>
        <div className="duck-targets">
          {project.tracks.map((track) => (
            <label key={track.id}>
              <input type="checkbox"
                checked={ducking.target_track_ids.includes(track.id)}
                onChange={() => toggleDuckingTarget(track.id)} />
              {track.name}
            </label>
          ))}
        </div>
        <div className="duck-modes">
          {([["off", "原始"], ["a", "设置 A"], ["b", "设置 B"]] as const).map(([mode, label]) => (
            <label key={mode} className={ducking.active_preset_id === mode ? "duck-mode-active" : ""}>
              <input type="radio" name="ducking-mode"
                checked={ducking.active_preset_id === mode}
                onChange={() => updateDucking({ active_preset_id: mode })} />{label}
            </label>
          ))}
        </div>
      </div>
      {activePreset && (
        <div className="ducking-params">
          <label>阈值 {activePreset.threshold_db.toFixed(0)} dB
            <input type="range" min="-60" max="0" value={activePreset.threshold_db}
              onChange={(e) => updateDuckingPreset(activePreset.id, { threshold_db: Number(e.target.value) })} /></label>
          <label>深度 {activePreset.depth_db.toFixed(0)} dB
            <input type="range" min="-40" max="-1" value={activePreset.depth_db}
              onChange={(e) => updateDuckingPreset(activePreset.id, { depth_db: Number(e.target.value) })} /></label>
        </div>
      )}
      <div className="duck-actions">
        <button type="button" disabled={busy} onClick={analyze}>检测混音</button>
        <button type="button" onClick={useStudio.getState().ensureDuckingV2}>升级为多角色编排</button>
        <span>{message}</span>
        {legacyReport && <span>压低 {legacyReport.ducking_ranges.length} 段，峰值 {peakDb(legacyReport)}</span>}
      </div>
    </section>
  );
}

function RoleCard({ role, tracks }: {
  role: DuckingConfigV2["plans"][number]["roles"][number];
  tracks: { id: string; name: string; muted: boolean }[];
}) {
  const { updateRole, removeRole, toggleRoleTrack } = useStudio();
  return (
    <div className="duck-role">
      <div className="duck-role-head">
        <input className="duck-role-name" value={role.name}
          onChange={(e) => updateRole(role.id, { name: e.target.value })} />
        <label className="duck-inline">优先级
          <input type="number" value={role.priority}
            onChange={(e) => updateRole(role.id, { priority: Number(e.target.value) })} /></label>
        <label className="duck-inline">控制信号
          <select value={role.source}
            onChange={(e) => updateRole(role.id, { source: e.target.value as "pre" | "post" })}>
            <option value="pre">处理前（原始）</option>
            <option value="post">处理后（已被压低）</option>
          </select></label>
        <button type="button" className="duck-danger-btn" onClick={() => removeRole(role.id)}>删除角色</button>
      </div>
      <div className="duck-role-tracks">
        <div>
          <span>控制音轨（可多选，重复归入只算一次）</span>
          <div className="duck-targets">
            {tracks.map((track) => (
              <label key={track.id}>
                <input type="checkbox"
                  checked={role.control_track_ids.includes(track.id)}
                  onChange={() => toggleRoleTrack(role.id, track.id, "control")} />
                {track.name}{track.muted ? "（静音仍控制）" : ""}
              </label>
            ))}
          </div>
        </div>
        <div>
          <span>压低这些音轨</span>
          <div className="duck-targets">
            {tracks.map((track) => (
              <label key={track.id}>
                <input type="checkbox"
                  checked={role.target_track_ids.includes(track.id)}
                  onChange={() => toggleRoleTrack(role.id, track.id, "target")} />
                {track.name}
              </label>
            ))}
          </div>
        </div>
      </div>
      <div className="ducking-params">
        <label>阈值 {role.threshold_db.toFixed(0)} dB
          <input type="range" min="-60" max="0" value={role.threshold_db}
            onChange={(e) => updateRole(role.id, { threshold_db: Number(e.target.value) })} /></label>
        <label>深度 {role.depth_db.toFixed(0)} dB
          <input type="range" min="-40" max="-1" value={role.depth_db}
            onChange={(e) => updateRole(role.id, { depth_db: Number(e.target.value) })} /></label>
        <label>进入 {role.attack_sec.toFixed(2)}s
          <input type="number" min={0} max={10} step={0.01} value={role.attack_sec}
            onChange={(e) => updateRole(role.id, { attack_sec: Number(e.target.value) })} /></label>
        <label>保持 {role.hold_sec.toFixed(2)}s
          <input type="number" min={0} max={10} step={0.01} value={role.hold_sec}
            onChange={(e) => updateRole(role.id, { hold_sec: Number(e.target.value) })} /></label>
        <label>恢复 {role.release_sec.toFixed(2)}s
          <input type="number" min={0} max={30} step={0.01} value={role.release_sec}
            onChange={(e) => updateRole(role.id, { release_sec: Number(e.target.value) })} /></label>
      </div>
    </div>
  );
}

export function DuckingPanel() {
  const { project, setActivePlan, updateActivePlan, addRole, savePlanCopy, removePlan } = useStudio();
  const [report, setReport] = useState<PreviewReport | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const ducking = project.ducking;

  if (!isV2(ducking)) {
    return <LegacyPanel />;
  }
  const plan = ducking.plans.find((item) => item.id === ducking.active_plan_id) ?? ducking.plans[0];

  async function analyze() {
    setBusy(true);
    setMessage("正在检测……");
    try {
      const next = useStudio.getState().project;
      const result = await api.preview(next);
      setReport(result);
      setMessage("检测完成：最终峰值 " + peakDb(result));
    } catch (error) {
      setReport(null);
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const v2Report = report?.ducking_report;

  return (
    <section className="ducking-panel">
      <div className="ducking-title">
        <strong>多控制源闪避编排</strong>
        <span>同一节目里主持人、远端嘉宾、提示音可分属不同角色；静音的控制轨仍参与触发。
          交接按攻击/保持/恢复逐采样连续，一人停顿不会忽略仍在讲话的另一人。</span>
      </div>

      <div className="duck-plans">
        {ducking.plans.map((item) => (
          <label key={item.id} className={item.id === plan.id ? "duck-mode-active" : ""}>
            <input type="radio" name="duck-plan" checked={item.id === plan.id}
              onChange={() => setActivePlan(item.id)} />
            {item.name}
          </label>
        ))}
        <button type="button" onClick={savePlanCopy}>另存副本并对照</button>
        <button type="button" disabled={ducking.plans.length <= 1}
          onClick={() => removePlan(plan.id)}>删除当前方案</button>
        <label className="duck-inline">合成规则
          <select value={plan.combine}
            onChange={(e) => updateActivePlan({ combine: e.target.value as "strongest" | "multiply" })}>
            <option value="strongest">较强一方主导（逐采样取压得更深者）</option>
            <option value="multiply">共同作用（增益相乘 / dB 相加）</option>
          </select></label>
      </div>

      <div className="duck-plan-name">
        <label>方案名
          <input value={plan.name} onChange={(e) => updateActivePlan({ name: e.target.value })} /></label>
      </div>

      <div className="duck-roles">
        {plan.roles.map((role) => (
          <RoleCard key={role.id} role={role} tracks={project.tracks.map((t) => ({
            id: t.id, name: t.name, muted: t.muted,
          }))} />
        ))}
      </div>
      <div className="duck-actions">
        <button type="button" onClick={addRole}>添加角色</button>
        <button type="button" disabled={busy} onClick={analyze}>检测混音 / 核对来源交接</button>
        <span className={message.includes("环") || message.includes("重复") ? "duck-error-text" : ""}>{message}</span>
      </div>

      {v2Report && v2Report.plan_id === plan.id && (
        <div className="duck-report-v2">
          <strong>逐受控轨核对（合成规则：{v2Report.combine === "strongest" ? "较强主导" : "共同作用"}）</strong>
          {v2Report.targets.map((target) => {
            const track = project.tracks.find((t) => t.id === target.track_id);
            return (
              <div key={target.track_id} className="duck-target-report">
                <span>{track?.name ?? target.track_id}{target.audible ? "" : "（当前被静音/独听裁决排除）"}</span>
                <span>实际峰值 {(20 * Math.log10(Math.max(target.peak, 1e-9))).toFixed(1)} dBFS</span>
                <span>最深增益 {(20 * Math.log10(Math.max(target.min_gain, 1e-9))).toFixed(1)} dB</span>
                <div className="duck-ranges">
                  {target.ranges.slice(0, 12).map((range, i) => (
                    <span key={i}>{range.start.toFixed(2)}–{range.end.toFixed(2)}s 由 [{sourceLabel(range, v2Report)}] 控制</span>
                  ))}
                  {target.ranges.length > 12 && <span>另有 {target.ranges.length - 12} 段</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {report && (!v2Report || v2Report.plan_id !== plan.id) && (
        <div className="duck-message">报告属于另一方案，请在当前方案重新检测。</div>
      )}
    </section>
  );
}
