import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import type { DuckingConfig, DuckingConfigV2, DuckingReport, PreviewReport, ProjectData } from "../types";

function isV2(ducking: DuckingConfig | DuckingConfigV2 | null | undefined): ducking is DuckingConfigV2 {
  return !!ducking && (ducking as DuckingConfigV2).version === 2;
}

function peakDb(report: PreviewReport | undefined) {
  if (!report) return "—";
  return (20 * Math.log10(Math.max(report.peak, 1e-9))).toFixed(1) + " dBFS";
}

/** 一次“检测混音”的只读快照：保留检测当时的角色名/轨名，
 *  之后改名、删角色、删方案都不影响这份快照的展示与导出。 */
interface ReportSnapshot {
  id: number;
  time: string;
  planId: string;
  planName: string;
  combine: "strongest" | "multiply";
  report: DuckingReport;
  roleNames: Record<string, string>;
  trackNames: Record<string, string>;
}

function takeSnapshot(id: number, project: ProjectData, report: DuckingReport): ReportSnapshot {
  const ducking = project.ducking as DuckingConfigV2 | null | undefined;
  const plan = ducking && ducking.version === 2
    ? ducking.plans.find((item) => item.id === report.plan_id)
    : undefined;
  return {
    id,
    time: new Date().toLocaleTimeString(),
    planId: report.plan_id,
    planName: plan?.name ?? report.plan_id,
    combine: report.combine,
    report,
    roleNames: Object.fromEntries(report.roles.map((role) => [role.id, role.name])),
    trackNames: Object.fromEntries(project.tracks.map((track) => [track.id, track.name])),
  };
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value;
}

function downloadCsv(filename: string, rows: string[][]) {
  const body = "\uFEFF" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
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
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [snapshots, setSnapshots] = useState<ReportSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filterRole, setFilterRole] = useState("");
  const [filterTrack, setFilterTrack] = useState("");
  const [filterStart, setFilterStart] = useState("");
  const [filterEnd, setFilterEnd] = useState("");
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
      if (result.ducking_report) {
        const snapshot = takeSnapshot(Date.now(), next, result.ducking_report);
        setSnapshots((prev) => [...prev, snapshot]);
        setSelectedId(snapshot.id);
        setFilterRole("");
        setFilterTrack("");
        setFilterStart("");
        setFilterEnd("");
      }
      setMessage("检测完成：最终峰值 " + peakDb(result));
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const snapshot = snapshots.find((item) => item.id === selectedId) ?? snapshots[snapshots.length - 1];
  // 角色改名后，新导出一律使用当前方案里的名字；快照里没有的角色
  // （已删除/属于别的方案）保留检测当时的名字。
  function displayRoleName(snap: ReportSnapshot, roleId: string): string {
    if (snap.planId === plan.id) {
      const current = plan.roles.find((role) => role.id === roleId);
      if (current) return current.name;
    }
    return snap.roleNames[roleId] ?? roleId;
  }
  function displayTrackName(snap: ReportSnapshot, trackId: string): string {
    return project.tracks.find((track) => track.id === trackId)?.name
      ?? snap.trackNames[trackId] ?? trackId;
  }

  const startBound = filterStart.trim() === "" ? -Infinity : Number(filterStart);
  const endBound = filterEnd.trim() === "" ? Infinity : Number(filterEnd);
  const rows = snapshot ? snapshot.report.targets.flatMap((target) =>
    target.ranges
      .filter((range) =>
        (!filterTrack || target.track_id === filterTrack) &&
        (!filterRole || range.control_role_ids.includes(filterRole)) &&
        range.end > startBound && range.start < endBound)
      .map((range) => ({ target, range })),
  ) : [];

  function exportCsv() {
    if (!snapshot) return;
    const data = rows.map(({ target, range }) => [
      displayTrackName(snapshot, target.track_id),
      range.control_role_ids.map((id) => displayRoleName(snapshot, id)).join("+") || (range.control ?? "—"),
      range.start.toFixed(3),
      range.end.toFixed(3),
      range.depth_db.toFixed(1),
    ]);
    downloadCsv("ducking-ranges.csv", [
      ["受控音轨", "控制角色", "开始(秒)", "结束(秒)", "压低量(dB)"],
      ...data,
    ]);
  }

  // 同名角色在筛选下拉里用 id 后缀区分。
  const roleOptions = snapshot ? snapshot.report.roles.map((role) => {
    const duplicated = snapshot.report.roles.filter((item) => item.name === role.name).length > 1;
    return { id: role.id, label: duplicated ? role.name + "（" + role.id + "）" : role.name };
  }) : [];

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

      {snapshots.length > 0 && snapshot && (
        <div className="duck-report-v2">
          <div className="duck-snapshot-bar">
            <strong>检测快照</strong>
            <select value={snapshot.id} onChange={(e) => setSelectedId(Number(e.target.value))}>
              {snapshots.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.time} · {item.planName}（{item.combine === "strongest" ? "较强主导" : "共同作用"}）
                </option>
              ))}
            </select>
            {snapshot.planId !== plan.id && (
              <span className="duck-message">该快照属于另一方案，保留检测当时的角色名。</span>
            )}
          </div>

          <div className="duck-filters">
            <label>控制角色
              <select value={filterRole} onChange={(e) => setFilterRole(e.target.value)}>
                <option value="">全部</option>
                {roleOptions.map((role) => (
                  <option key={role.id} value={role.id}>{role.label}</option>
                ))}
              </select>
            </label>
            <label>受控音轨
              <select value={filterTrack} onChange={(e) => setFilterTrack(e.target.value)}>
                <option value="">全部</option>
                {snapshot.report.targets.map((target) => (
                  <option key={target.track_id} value={target.track_id}>
                    {displayTrackName(snapshot, target.track_id)}
                  </option>
                ))}
              </select>
            </label>
            <label>起始(秒)
              <input type="number" step="0.1" value={filterStart} placeholder="不限"
                onChange={(e) => setFilterStart(e.target.value)} /></label>
            <label>结束(秒)
              <input type="number" step="0.1" value={filterEnd} placeholder="不限"
                onChange={(e) => setFilterEnd(e.target.value)} /></label>
            <button type="button" disabled={rows.length === 0} onClick={exportCsv}>
              导出 CSV（{rows.length} 段）
            </button>
          </div>

          <div className="duck-ranges-full">
            {rows.length === 0 && <span>当前筛选条件下没有交接区间。</span>}
            {rows.map(({ target, range }, i) => (
              <div key={i} className="duck-range-row">
                <span>{displayTrackName(snapshot, target.track_id)}{target.audible ? "" : "（被静音/独听排除）"}</span>
                <span>{range.start.toFixed(2)}–{range.end.toFixed(2)}s</span>
                <span>由 [{range.control_role_ids.map((id) => displayRoleName(snapshot, id)).join("+") || (range.control ?? "—")}] 控制</span>
                <span>压低 {range.depth_db.toFixed(1)} dB</span>
              </div>
            ))}
          </div>

          <div className="duck-target-summary">
            {snapshot.report.targets.map((target) => (
              <div key={target.track_id} className="duck-target-report">
                <span>{displayTrackName(snapshot, target.track_id)}{target.audible ? "" : "（当前被静音/独听裁决排除）"}</span>
                <span>实际峰值 {(20 * Math.log10(Math.max(target.peak, 1e-9))).toFixed(1)} dBFS</span>
                <span>最深增益 {(20 * Math.log10(Math.max(target.min_gain, 1e-9))).toFixed(1)} dB</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
