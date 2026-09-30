import { useStudio } from "../store";
import type { AutomationPointData, EffectData } from "../types";

const EFFECT_PRESETS: { label: string; effect: EffectData }[] = [
  { label: "+ 增益", effect: { type: "gain", gain_db: 6, amount: 1 } },
  { label: "+ 延迟/回声", effect: { type: "delay", delay_sec: 0.25, feedback: 0.3, wet: 0.4, amount: 1 } },
];

function PointRows({
  points, mode, onChange, onRemove,
}: {
  points: AutomationPointData[];
  mode: "clip" | "track";
  onChange: (next: AutomationPointData[]) => void;
  onRemove: (index: number) => void;
}) {
  function update(index: number, patch: Partial<AutomationPointData>) {
    onChange(points.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  }
  return (
    <div className="points">
      <div className="points-head">
        <span>时间{mode === "clip" ? "（素材相对秒）" : "（工程秒）"}</span>
        <span>音量 dB</span>
        <span>声像 L-R</span>
        {mode === "track" && <span>效果强度</span>}
        <span />
      </div>
      {points.length === 0 && <div className="points-empty">暂无断点</div>}
      {[...points].sort((a, b) => a.time - b.time).map((point) => {
        const index = points.indexOf(point);
        return (
          <div className="points-row" key={`${point.time}-${index}`}>
            <input type="number" step="0.01" min="0" value={point.time}
              onChange={(e) => update(index, { time: Number(e.target.value) })} />
            <input type="number" step="0.5" placeholder="—"
              value={point.gain_db ?? ""} onChange={(e) =>
                update(index, { gain_db: e.target.value === "" ? null : Number(e.target.value) })} />
            <input type="number" step="0.1" min="-1" max="1" placeholder="—"
              value={point.pan ?? ""} onChange={(e) =>
                update(index, { pan: e.target.value === "" ? null : Number(e.target.value) })} />
            {mode === "track" && (
              <input type="number" step="0.1" min="0" max="1" placeholder="—"
                value={point.amount ?? ""} onChange={(e) =>
                  update(index, { amount: e.target.value === "" ? null : Number(e.target.value) })} />
            )}
            <button onClick={() => onRemove(index)} title="删除断点">×</button>
          </div>
        );
      })}
    </div>
  );
}

export function Inspector() {
  const {
    project, selected,
    setClipGain, setClipFades, setClipAutomation,
    setTrackGain, setTrackPan, setTrackAutomation,
    addEffect, updateEffect, removeEffect,
  } = useStudio();

  if (!selected) {
    return (
      <aside className="inspector">
        <h3>检查器</h3>
        <p className="hint">选择一个素材可编辑渐变与素材内分段音量/声像；选中素材后在此编辑轨道自动化与效果器链。</p>
      </aside>
    );
  }
  const track = project.tracks.find((t) => t.id === selected.trackId);
  const clip = track?.clips.find((c) => c.id === selected.clipId);
  if (!track || !clip) return null;
  const duration = clip.out_point - clip.in_point;

  function addPoint(target: "clip" | "track") {
    if (target === "clip") {
      setClipAutomation(track!.id, clip!.id, [...clip!.automation, { time: 0, gain_db: 0 }]);
    } else {
      const lastEnd = Math.max(0, ...track!.clips.map((c) => c.start + c.out_point - c.in_point));
      setTrackAutomation(track!.id, [...track!.automation, { time: lastEnd, gain_db: track!.gain_db }]);
    }
  }

  return (
    <aside className="inspector">
      <h3>检查器 · {track.name}</h3>

      <section>
        <h4>素材 {clip.source.split(".")[0]}</h4>
        <label className="field">
          素材增益 {clip.gain_db.toFixed(1)} dB
          <input type="range" min="-24" max="12" step="0.5" value={clip.gain_db}
            onChange={(e) => setClipGain(track.id, clip.id, Number(e.target.value))} />
        </label>
        <div className="fade-row">
          <label>淡入秒
            <input type="number" min="0" step="0.05" defaultValue="0.1" id={`fadein-${clip.id}`} />
          </label>
          <label>淡出秒
            <input type="number" min="0" step="0.05" defaultValue="0.1" id={`fadeout-${clip.id}`} />
          </label>
          <button onClick={() => {
            const fi = Number((document.getElementById(`fadein-${clip.id}`) as HTMLInputElement).value) || 0;
            const fo = Number((document.getElementById(`fadeout-${clip.id}`) as HTMLInputElement).value) || 0;
            setClipFades(track.id, clip.id, Math.min(fi, duration), Math.min(fo, duration));
          }}>应用渐变</button>
        </div>
        <div className="points-toolbar">
          <span>素材内分段音量/声像（随片段移动）</span>
          <button onClick={() => addPoint("clip")}>+ 断点</button>
        </div>
        <PointRows points={clip.automation} mode="clip"
          onChange={(pts) => setClipAutomation(track.id, clip.id, pts)}
          onRemove={(i) => setClipAutomation(track.id, clip.id, clip.automation.filter((_, idx) => idx !== i))} />
      </section>

      <section>
        <h4>轨道</h4>
        <label className="field">
          推子 {track.gain_db.toFixed(1)} dB
          <input type="range" min="-24" max="12" step="0.5" value={track.gain_db}
            onChange={(e) => setTrackGain(track.id, Number(e.target.value))} />
        </label>
        <label className="field">
          声像 {track.pan === 0 ? "居中" : track.pan < 0 ? `左 ${Math.abs(track.pan).toFixed(2)}` : `右 ${track.pan.toFixed(2)}`}
          <input type="range" min="-1" max="1" step="0.05" value={track.pan}
            onChange={(e) => setTrackPan(track.id, Number(e.target.value))} />
        </label>

        <div className="effects-head">
          <span>效果器链（按顺序执行）</span>
          <span>
            {EFFECT_PRESETS.map((preset) => (
              <button key={preset.label} onClick={() => addEffect(track.id, { ...preset.effect })}>{preset.label}</button>
            ))}
          </span>
        </div>
        {track.effects.map((effect, i) => (
          <div className="effect-card" key={i}>
            <div className="effect-title">
              <strong>{effect.type === "gain" ? `增益 ${effect.gain_db ?? 0} dB` : "延迟/回声"}</strong>
              <button onClick={() => removeEffect(track.id, i)}>删</button>
            </div>
            {effect.type === "gain" ? (
              <label className="field">
                增益 {effect.gain_db ?? 0} dB
                <input type="range" min="-24" max="24" step="0.5" value={effect.gain_db ?? 0}
                  onChange={(e) => updateEffect(track.id, i, { gain_db: Number(e.target.value) })} />
              </label>
            ) : (
              <div className="effect-grid">
                <label>延迟秒
                  <input type="number" step="0.05" min="0" value={effect.delay_sec ?? 0.25}
                    onChange={(e) => updateEffect(track.id, i, { delay_sec: Number(e.target.value) })} />
                </label>
                <label>反馈
                  <input type="number" step="0.05" min="0" max="0.9" value={effect.feedback ?? 0.3}
                    onChange={(e) => updateEffect(track.id, i, { feedback: Number(e.target.value) })} />
                </label>
                <label>湿声
                  <input type="number" step="0.05" min="0" max="1" value={effect.wet ?? 0.4}
                    onChange={(e) => updateEffect(track.id, i, { wet: Number(e.target.value) })} />
                </label>
              </div>
            )}
            <label className="field">
              基础处理强度 {(effect.amount ?? 1).toFixed(2)}
              <input type="range" min="0" max="1" step="0.05" value={effect.amount ?? 1}
                onChange={(e) => updateEffect(track.id, i, { amount: Number(e.target.value) })} />
            </label>
          </div>
        ))}

        <div className="points-toolbar">
          <span>轨道自动化（绝对工程时间，不随片段移动）</span>
          <button onClick={() => addPoint("track")}>+ 断点</button>
        </div>
        <PointRows points={track.automation} mode="track"
          onChange={(pts) => setTrackAutomation(track.id, pts)}
          onRemove={(i) => setTrackAutomation(track.id, track.automation.filter((_, idx) => idx !== i))} />
      </section>
    </aside>
  );
}
