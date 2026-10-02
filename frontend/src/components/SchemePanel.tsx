import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import type { DuckCombineMode, DuckingValidation, PreviewReport } from "../types";

const COMBINE_LABELS: [DuckCombineMode, string][] = [
  ["strongest", "较强者主导"],
  ["priority", "优先级主导"],
  ["multiply", "共同作用（压低量相乘）"],
];

export function SchemePanel() {
  const {
    project, ensureDucking, addScheme, duplicateScheme, removeScheme, setActiveScheme,
    updateScheme, addRole, updateRole, removeRole, toggleRoleTrack,
  } = useStudio();
  const [report, setReport] = useState<PreviewReport | null>(null);
  const [validation, setValidation] = useState<DuckingValidation | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const ducking = project.ducking;
  const schemes = ducking?.schemes ?? [];
  const activeId = ducking?.active_scheme_id ?? null;
  const scheme = schemes.find((item) => item.id === activeId) ?? schemes[0];

  async function withProject(fn: (proj: typeof project) => Promise<void>) {
    ensureDucking();
    setBusy(true);
    try {
      await fn(useStudio.getState().project);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const checkRelations = () => withProject(async (proj) => {
    await api.saveProject(proj);
    setValidation(await api.validateDucking(proj));
    setMessage("控制关系检查完成");
  });

  const analyze = () => withProject(async (proj) => {
    await api.saveProject(proj);
    const result = await api.preview(proj);
    setReport(result);
    setMessage("检测完成：峰值 " + (20 * Math.log10(Math.max(result.peak, 1e-9))).toFixed(1) + " dBFS");
  });

  return (
    <section className="ducking-panel scheme-panel">
      <div className="ducking-title">
        <strong>多控制源闪避方案（主持人 / 远端嘉宾 / 提示音等角色共同控制音乐）</strong>
        <span>
          每条方案是自包含快照：修改角色、优先级或音轨位置后，可复制方案对照，
          已保存的其他方案不随当前方案变化。启用方案后上面的单控制轨设置不再生效。
        </span>
      </div>

      <div className="scheme-bar">
        <label>启用方案
          <select
            value={activeId ?? ""}
            onChange={(event) => setActiveScheme(event.target.value || null)}
            onFocus={ensureDucking}
          >
            <option value="">不启用（使用上方单控制轨设置）</option>
            {schemes.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => { ensureDucking(); addScheme(); }}>新建方案</button>
        {scheme && (
          <>
            <button type="button" onClick={() => duplicateScheme(scheme.id)}>另存为副本</button>
            <button type="button" onClick={() => removeScheme(scheme.id)}>删除方案</button>
          </>
        )}
      </div>

      {scheme && (
        <>
          <div className="ducking-grid scheme-grid">
            <label>方案名
              <input
                type="text"
                value={scheme.name}
                onChange={(event) => updateScheme(scheme.id, { name: event.target.value })}
              />
            </label>
            <label>多角色同时发声时
              <select
                value={scheme.combine_mode}
                onChange={(event) => updateScheme(scheme.id, { combine_mode: event.target.value as DuckCombineMode })}
              >
                {COMBINE_LABELS.map(([mode, label]) => (
                  <option key={mode} value={mode}>{label}</option>
                ))}
              </select>
            </label>
            <label>交接过渡 {scheme.handover_sec.toFixed(2)}s
              <input type="number" min="0" max="5" step="0.01" value={scheme.handover_sec}
                onChange={(e) => updateScheme(scheme.id, { handover_sec: Number(e.target.value) })} />
            </label>
            <label>触发阈值 {scheme.threshold_db.toFixed(0)} dB
              <input type="range" min="-60" max="0" step="1" value={scheme.threshold_db}
                onChange={(e) => updateScheme(scheme.id, { threshold_db: Number(e.target.value) })} />
            </label>
            <label>进入 {scheme.attack_sec.toFixed(2)}s
              <input type="number" min="0" max="10" step="0.01" value={scheme.attack_sec}
                onChange={(e) => updateScheme(scheme.id, { attack_sec: Number(e.target.value) })} />
            </label>
            <label>保持 {scheme.hold_sec.toFixed(2)}s
              <input type="number" min="0" max="10" step="0.01" value={scheme.hold_sec}
                onChange={(e) => updateScheme(scheme.id, { hold_sec: Number(e.target.value) })} />
            </label>
            <label>恢复 {scheme.release_sec.toFixed(2)}s
              <input type="number" min="0" max="30" step="0.01" value={scheme.release_sec}
                onChange={(e) => updateScheme(scheme.id, { release_sec: Number(e.target.value) })} />
            </label>
          </div>

          <div className="scheme-roles">
            <div className="scheme-roles-head">
              <strong>角色（控制音轨归组，组内重复归组只算一次）</strong>
              <button type="button" onClick={() => addRole(scheme.id)}>添加角色</button>
            </div>
            {scheme.roles.map((role) => (
              <div key={role.id} className="scheme-role">
                <div className="scheme-role-head">
                  <input
                    type="text"
                    value={role.name}
                    onChange={(event) => updateRole(scheme.id, role.id, { name: event.target.value })}
                  />
                  <label>压低 {role.depth_db.toFixed(0)} dB
                    <input type="range" min="-40" max="-1" step="1" value={role.depth_db}
                      onChange={(e) => updateRole(scheme.id, role.id, { depth_db: Number(e.target.value) })} />
                  </label>
                  <label>优先级
                    <input type="number" min="0" max="99" step="1" value={role.priority}
                      onChange={(e) => updateRole(scheme.id, role.id, { priority: Number(e.target.value) })} />
                  </label>
                  <label>控制信号取数点
                    <select
                      value={role.tap}
                      onChange={(event) => updateRole(scheme.id, role.id, { tap: event.target.value as "pre" | "post" })}
                    >
                      <option value="pre">处理前（原始声音）</option>
                      <option value="post">处理后（被压低后的声音）</option>
                    </select>
                  </label>
                  <button type="button" onClick={() => removeRole(scheme.id, role.id)}>删除</button>
                </div>
                <div className="scheme-role-tracks">
                  <div className="duck-targets">
                    <span>控制音轨</span>
                    {project.tracks.map((track) => (
                      <label key={track.id}>
                        <input
                          type="checkbox"
                          checked={role.control_track_ids.includes(track.id)}
                          onChange={() => toggleRoleTrack(scheme.id, role.id, "control_track_ids", track.id)}
                        />
                        {track.name}{track.muted ? "（静音但仍控制）" : ""}
                      </label>
                    ))}
                  </div>
                  <div className="duck-targets">
                    <span>压低目标</span>
                    {project.tracks.map((track) => (
                      <label key={track.id}>
                        <input
                          type="checkbox"
                          checked={role.target_track_ids.includes(track.id)}
                          onChange={() => toggleRoleTrack(scheme.id, role.id, "target_track_ids", track.id)}
                        />
                        {track.name}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="scheme-actions">
            <button type="button" disabled={busy} onClick={checkRelations}>检查控制关系</button>
            <button type="button" disabled={busy} onClick={analyze}>检测方案</button>
          </div>

          {validation?.schemes.filter((item) => item.id === scheme.id).map((item) => (
            <div key={item.id} className={item.ok ? "scheme-validation" : "scheme-validation duck-danger"}>
              {!item.ok && (
                <div>无法成立：{item.cycle.join(" → ")} 形成相互控制，已整体拒绝，不会只计算一部分。</div>
              )}
              {item.roles.map((role) => (
                <div key={role.id}>
                  {role.name}：取数点 {role.tap_label}
                  {role.depends_on.length > 0 &&
                    "；依赖 " + role.depends_on.map((dep) => dep.name).join("、") + " 先完成压低"}
                </div>
              ))}
            </div>
          ))}

          {report && (
            <div className="duck-ranges">
              <span>当前由谁控制：</span>
              {report.ducking_segments.length === 0 && <span>全程无人触发</span>}
              {report.ducking_segments.slice(0, 12).map((seg, i) => (
                <span key={i}>{seg.start.toFixed(2)}–{seg.end.toFixed(2)}s {seg.role_name}</span>
              ))}
              {report.ducking_segments.length > 12 && <span>另有 {report.ducking_segments.length - 12} 段</span>}
            </div>
          )}
          {message && <div className="duck-message">{message}</div>}
        </>
      )}
    </section>
  );
}
