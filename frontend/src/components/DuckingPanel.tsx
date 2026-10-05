import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import type { DuckingConfig, DuckingConfigV2, PlannerResult, PreviewReport } from "../types";
import {
  type DuckingSnapshot, type RangeFilters, emptyFilters,
  flattenRanges, filterRanges, roleOptions, roleLabel, deletedRoles,
  rangesToCsv, downloadCsv,
} from "../duckingReport";

function isV2(ducking: DuckingConfig | DuckingConfigV2 | null | undefined): ducking is DuckingConfigV2 {
  return !!ducking && (ducking as DuckingConfigV2).version === 2;
}

function peakDb(report: PreviewReport | undefined) {
  if (!report) return "—";
  return (20 * Math.log10(Math.max(report.peak, 1e-9))).toFixed(1) + " dBFS";
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
        <label>可听度下限 {role.floor_db === null || role.floor_db === undefined ? "不约束" : `${role.floor_db.toFixed(0)} dB`}
          <input type="number" min={-60} max={0} step={1}
            value={role.floor_db ?? ""}
            placeholder="不约束"
            onChange={(e) => updateRole(role.id, {
              floor_db: e.target.value === "" ? null : Number(e.target.value),
            })} /></label>
        <label>允许恢复时长 {role.max_recovery_sec === null || role.max_recovery_sec === undefined ? "不约束" : `${role.max_recovery_sec.toFixed(2)}s`}
          <input type="number" min={0.01} max={30} step={0.01}
            value={role.max_recovery_sec ?? ""}
            placeholder="不约束"
            onChange={(e) => updateRole(role.id, {
              max_recovery_sec: e.target.value === "" ? null : Number(e.target.value),
            })} /></label>
      </div>
    </div>
  );
}

function PlannerSection({ plan }: { plan: DuckingConfigV2["plans"][number] }) {
  const { project, addDuckingApproval, clearDuckingApprovals } = useStudio();
  const [result, setResult] = useState<PlannerResult | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [auditionId, setAuditionId] = useState("");
  const ducking = project.ducking as DuckingConfigV2;
  const approvals = ducking.approvals ?? [];
  const trackName = (id: string) => project.tracks.find((t) => t.id === id)?.name ?? id;
  const roleName = (id: string) => plan.roles.find((r) => r.id === id)?.name ?? id;

  async function generate() {
    setBusy(true);
    setMessage("正在生成候选包络方案……");
    try {
      const next = await api.planDucking(useStudio.getState().project);
      setResult(next);
      setMessage(next.conflicts.length
        ? `生成完成：${next.candidates.length} 个候选，${next.conflicts.length} 处冲突需人工处理`
        : `生成完成：${next.candidates.length} 个可比较候选，无冲突`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function audition(candidateId: string) {
    setBusy(true);
    try {
      // 试听只读：候选与原混音两份预览对照，工程保持不变。
      const outcome = await api.auditionDucking(useStudio.getState().project, candidateId);
      setAuditionId(candidateId);
      const fmt = (peak: number) => (20 * Math.log10(Math.max(peak, 1e-9))).toFixed(1);
      setMessage(`试听 ${candidateId}：候选峰值 ${fmt(outcome.preview.peak)} dBFS，` +
        `原混音峰值 ${fmt(outcome.original.peak)} dBFS（工程未修改）`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function approve(candidateId: string, targetId: string, start: number, end: number) {
    addDuckingApproval({ plan_id: plan.id, candidate: candidateId, target_id: targetId, start, end });
    setMessage(`已批准 ${candidateId} 在 ${trackName(targetId)} 的 ${start.toFixed(2)}–${end.toFixed(2)}s 段；` +
      "其余时间段保持当前方案不变");
  }

  return (
    <div className="duck-planner">
      <div className="ducking-title">
        <strong>压低方案规划（交错角色）</strong>
        <span>根据重叠交接区间生成可比较的候选包络；冲突只明示不自动选择；
          试听保留原混音，可按时间段批准，边界连续、未批准段不变。</span>
      </div>
      <div className="duck-actions">
        <button type="button" disabled={busy} onClick={generate}>生成候选包络方案</button>
        {approvals.length > 0 && (
          <button type="button" onClick={() => { clearDuckingApprovals(); setMessage("已清除全部批准时间段"); }}>
            清除全部批准（{approvals.length}）
          </button>
        )}
        <span className={result?.conflicts.length ? "duck-error-text" : ""}>{message}</span>
      </div>
      {result && result.conflicts.length > 0 && (
        <div className="duck-message duck-error-text">
          {result.conflicts.map((conflict, i) => (
            <div key={i}>[{conflict.type === "ring" ? "环形控制" : "可听度冲突"}] {conflict.message}</div>
          ))}
        </div>
      )}
      {result && result.candidates.length > 0 && (
        <div className="duck-ranges-table-wrap">
          <table className="duck-ranges-table">
            <thead>
              <tr>
                <th>候选方案</th><th>受控轨</th><th>实际峰值</th><th>最深增益</th>
                <th>恢复时刻</th><th>恢复约束</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {result.candidates.flatMap((candidate) =>
                candidate.targets.map((target) => (
                  <tr key={`${candidate.id}-${target.target_id}`}
                    className={auditionId === candidate.id ? "duck-mode-active" : ""}>
                    <td>{candidate.name}</td>
                    <td>{trackName(target.target_id)}</td>
                    <td>{(20 * Math.log10(Math.max(target.peak, 1e-9))).toFixed(1)} dBFS</td>
                    <td>{target.min_gain_db.toFixed(1)} dB</td>
                    <td>{target.recoveries.length === 0 ? "—" : target.recoveries
                      .map((r) => `${r.after.toFixed(2)}s→${r.recovered_at === null ? "未恢复" : r.recovered_at.toFixed(2) + "s"}`)
                      .join("；")}</td>
                    <td>{target.max_recovery_sec === null ? "不约束"
                      : target.recovery_ok ? `≤${target.max_recovery_sec}s ✓` : `超过 ${target.max_recovery_sec}s 限制`}</td>
                    <td>
                      <button type="button" disabled={busy}
                        onClick={() => audition(candidate.id)}>试听</button>
                    </td>
                  </tr>
                )))}
            </tbody>
          </table>
        </div>
      )}
      {result && result.segments.length > 0 && auditionId && (
        <div className="duck-ranges-table-wrap">
          <strong>按时间段批准（{result.segments.length} 个交接区间，当前试听：{auditionId}）</strong>
          <table className="duck-ranges-table">
            <thead>
              <tr><th>起始(s)</th><th>结束(s)</th><th>受控轨</th><th>活动角色</th><th>操作</th></tr>
            </thead>
            <tbody>
              {result.segments.map((seg, i) => {
                const approved = approvals.some((a) => a.candidate === auditionId &&
                  a.target_id === seg.target_id &&
                  Math.abs(a.start - seg.start) < 1e-6 && Math.abs(a.end - seg.end) < 1e-6);
                return (
                  <tr key={`${seg.target_id}-${seg.start}-${i}`}>
                    <td>{seg.start.toFixed(3)}</td>
                    <td>{seg.end.toFixed(3)}</td>
                    <td>{trackName(seg.target_id)}</td>
                    <td>{seg.active_roles.map(roleName).join(" + ")}{seg.overlap ? "（交叠）" : ""}</td>
                    <td>{approved ? "已批准" : (
                      <button type="button"
                        onClick={() => approve(auditionId, seg.target_id, seg.start, seg.end)}>
                        批准此段
                      </button>
                    )}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {approvals.length > 0 && (
        <div className="duck-message">
          已批准 {approvals.length} 段：
          {approvals.map((a, i) => (
            <span key={i}> {a.candidate}@{trackName(a.target_id)} {a.start.toFixed(2)}–{a.end.toFixed(2)}s；</span>
          ))}
          其余时间段保持当前方案不变。
        </div>
      )}
    </div>
  );
}

function SnapshotView({ snapshot, filters, onFiltersChange }: {
  snapshot: DuckingSnapshot;
  filters: RangeFilters;
  onFiltersChange: (next: RangeFilters) => void;
}) {
  const allRows = flattenRanges(snapshot);
  const rows = filterRanges(allRows, filters);
  const roles = roleOptions(snapshot);
  const roleNameById = new Map(roles.map((role) => [role.id, role]));
  const targets = snapshot.report.targets;
  const deleted = deletedRoles(snapshot);

  function exportCsv(scope: "filtered" | "all") {
    const data = scope === "all" ? allRows : rows;
    const content = rangesToCsv(data);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    downloadCsv(`ducking-ranges-${snapshot.plan_id}-${stamp}.csv`, content);
  }

  return (
    <div className="duck-snapshot">
      <div className="duck-filters">
        <label>控制角色
          <select value={filters.roleId}
            onChange={(e) => onFiltersChange({ ...filters, roleId: e.target.value })}>
            <option value="">全部角色</option>
            {roles.map((role) => (
              <option key={role.id} value={role.id}>{roleLabel(role)}</option>
            ))}
            {deleted.map((role) => (
              <option key={role.id} value={role.id}>{role.id}（已删除角色 · 旧快照）</option>
            ))}
          </select>
        </label>
        <label>受控音轨
          <select value={filters.targetTrackId}
            onChange={(e) => onFiltersChange({ ...filters, targetTrackId: e.target.value })}>
            <option value="">全部受控轨</option>
            {targets.map((target) => (
              <option key={target.track_id} value={target.track_id}>
                {target.track_name ?? target.track_id}
              </option>
            ))}
          </select>
        </label>
        <label>起始时间 ≥（秒）
          <input type="number" min={0} step={0.1} value={filters.startTime}
            onChange={(e) => onFiltersChange({ ...filters, startTime: e.target.value })} /></label>
        <label>结束时间 ≤（秒）
          <input type="number" min={0} step={0.1} value={filters.endTime}
            onChange={(e) => onFiltersChange({ ...filters, endTime: e.target.value })} /></label>
        <button type="button" onClick={() => onFiltersChange(emptyFilters)}>清除筛选</button>
        <span className="duck-filter-count">{rows.length} / {allRows.length} 个区间</span>
      </div>
      <div className="duck-export-row">
        <button type="button" disabled={rows.length === 0}
          onClick={() => exportCsv("filtered")}>导出筛选结果 CSV（{rows.length} 行）</button>
        <button type="button" disabled={allRows.length === 0}
          onClick={() => exportCsv("all")}>导出全部区间 CSV（{allRows.length} 行）</button>
        <span>含起止时间、受控轨/控制角色名与最深压低量；导出只读，不改变音频。</span>
      </div>
      <div className="duck-ranges-table-wrap">
        {rows.length === 0 ? (
          <div className="duck-message">当前筛选没有匹配的交接区间。</div>
        ) : (
          <table className="duck-ranges-table">
            <thead>
              <tr>
                <th>起始(s)</th><th>结束(s)</th><th>受控音轨</th>
                <th>控制角色</th><th>最深压低量</th><th>状态</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={`${row.trackId}-${row.start}-${i}`}>
                  <td>{row.start.toFixed(3)}</td>
                  <td>{row.end.toFixed(3)}</td>
                  <td>{row.trackName}</td>
                  <td>{row.control}</td>
                  <td>{row.maxReductionDb === null ? "—" : `${row.maxReductionDb.toFixed(1)} dB`}</td>
                  <td>{row.audible ? "可听见" : "静音/独听排除"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {deleted.length > 0 && (
        <div className="duck-message">
          该快照含 {deleted.length} 个已不在当前方案中的控制角色，
          名称定格在检测时刻，可继续筛选与导出；重新检测才会使用当前方案。
        </div>
      )}
      {filters.roleId && !roleNameById.has(filters.roleId) && !deleted.some((r) => r.id === filters.roleId) && (
        <div className="duck-message">所选控制角色未参与任何受控轨压低。</div>
      )}
    </div>
  );
}

export function DuckingPanel() {
  const { project, setActivePlan, updateActivePlan, addRole, savePlanCopy, removePlan } = useStudio();
  const [report, setReport] = useState<PreviewReport | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [snapshots, setSnapshots] = useState<DuckingSnapshot[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>("");
  const [filters, setFilters] = useState<RangeFilters>(emptyFilters);
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
      const activePlan = (next.ducking as DuckingConfigV2).plans
        .find((item) => item.id === (next.ducking as DuckingConfigV2).active_plan_id);
      const v2 = result.ducking_report;
      if (v2) {
        const snapshot: DuckingSnapshot = {
          id: `${v2.plan_id}-${Date.now()}`,
          detected_at: new Date().toLocaleString(),
          plan_id: v2.plan_id,
          plan_name: activePlan?.name ?? v2.plan_id,
          combine: v2.combine,
          report: v2,
        };
        setSnapshots((items) => [...items, snapshot]);
        setSelectedSnapshotId(snapshot.id);
        setFilters(emptyFilters);
        const total = v2.targets.reduce((sum, t) => sum + t.ranges.length, 0);
        setMessage(`检测完成：共 ${total} 个交接区间，最终峰值 ${peakDb(result)}（已存为可筛选/导出的快照）`);
      } else {
        setMessage("检测完成：最终峰值 " + peakDb(result));
      }
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

      <PlannerSection plan={plan} />

      {snapshots.length > 0 && (
        <div className="duck-snapshots">
          <div className="duck-snapshot-bar">
            <label>检测快照（名称定格在检测时刻；改名/删除角色只影响新检测）
              <select value={selectedSnapshotId}
                onChange={(e) => { setSelectedSnapshotId(e.target.value); setFilters(emptyFilters); }}>
                {snapshots.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.detected_at} · {item.plan_name} ·
                    {item.report.targets.reduce((sum, t) => sum + t.ranges.length, 0)} 段
                  </option>
                ))}
              </select>
            </label>
            <button type="button" onClick={() => {
              setSnapshots((items) => items.filter((item) => item.id !== selectedSnapshotId));
              setSelectedSnapshotId("");
            }}>删除此快照</button>
          </div>
          {(() => {
            const selected = snapshots.find((item) => item.id === selectedSnapshotId)
              ?? snapshots[snapshots.length - 1];
            if (!selected) return null;
            return (
              <div className="duck-report-v2">
                <strong>全部交接区间（{selected.plan_name} ·
                  {selected.combine === "strongest" ? "较强主导" : "共同作用"}）</strong>
                {selected.report.targets.map((target) => {
                  const audibleText = target.audible ? "" : "（当前被静音/独听裁决排除）";
                  return (
                    <div key={target.track_id} className="duck-target-summary">
                      <span>{target.track_name ?? target.track_id}{audibleText}</span>
                      <span>实际峰值 {(20 * Math.log10(Math.max(target.peak, 1e-9))).toFixed(1)} dBFS</span>
                      <span>最深增益 {(20 * Math.log10(Math.max(target.min_gain, 1e-9))).toFixed(1)} dB</span>
                      <span>{target.ranges.length} 个交接区间</span>
                    </div>
                  );
                })}
                <SnapshotView snapshot={selected} filters={filters} onFiltersChange={setFilters} />
              </div>
            );
          })()}
        </div>
      )}
      {report && (!v2Report || !snapshots.some((item) => item.plan_id === plan.id)) && (
        <div className="duck-message">报告属于另一方案，请在当前方案重新检测。</div>
      )}
    </section>
  );
}
