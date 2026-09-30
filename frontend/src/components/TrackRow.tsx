import type { TrackData } from "../types";
import { useStudio } from "../store";
import { ClipView } from "./ClipView";

interface Props {
  track: TrackData;
  pixelsPerSecond: number;
  timelineWidth: number;
  anySolo: boolean;
}

export function TrackRow({ track, pixelsPerSecond, timelineWidth, anySolo }: Props) {
  const {
    selected, select, toggleMute, toggleSolo, setTrackGain, removeTrack,
    moveClip, trimClipLive, trimClip, removeClip, addClipFade, addClipPoint, clearClipAutomation,
    addTrackPoint, addEffect, addEffectPoint,
  } = useStudio();

  function promptTrackPoint(kind: "volume" | "pan") {
    const time = window.prompt("控制点时间（工程秒，不随片段移动）：", "0.5");
    if (time === null) return;
    const value = window.prompt(kind === "volume" ? "音量倍率（0=静音，1=原音量）" : "声像（-1=左，0=中，1=右）：", kind === "volume" ? "1" : "0");
    if (value !== null) addTrackPoint(track.id, kind, Number(time), Number(value));
  }

  function promptAddEffect() {
    const type = window.prompt("效果类型：gain（增益）/ delay（延迟）/ lowpass（低通）", "gain");
    if (!type) return;
    addEffect(track.id, type === "delay"
      ? { type, time: 0.25, feedback: 0.25, mix: 0.3, amount_points: [] }
      : type === "lowpass"
        ? { type, cutoff_hz: 1000, amount_points: [] }
        : { type: "gain", gain_db: -6, amount_points: [] });
  }

  function promptEffectPoint(index: number) {
    const time = window.prompt("效果强度控制点时间（工程秒）：", "0.5");
    if (time === null) return;
    const value = window.prompt("处理强度（0=旁通，1=完整效果）：", "1");
    if (value !== null) addEffectPoint(track.id, index, Number(time), Number(value));
  }

  function promptClipPoint(clipId: string, kind: "volume" | "pan") {
    const time = window.prompt("控制点时间（片段内秒，随片段移动）：", "0.25");
    if (time === null) return;
    const value = window.prompt(kind === "volume" ? "音量倍率（0=静音，1=原音量）：" : "声像（-1=左，0=中，1=右）：", "0");
    if (value !== null) addClipPoint(track.id, clipId, kind, Number(time), Number(value));
  }

  return (
    <div className={`track ${track.muted ? "track-muted" : ""} ${anySolo && !track.solo ? "track-dimmed" : ""}`}>
      <div className="track-controls">
        <input
          className="track-name"
          value={track.name}
          onChange={(e) => useStudio.setState((state) => ({
            project: { ...state.project, tracks: state.project.tracks.map((t) => t.id === track.id ? { ...t, name: e.target.value } : t) },
          }))}
        />
        <div className="track-buttons">
          <button
            className={`btn-toggle ${track.muted ? "active-danger" : ""}`}
            onClick={() => toggleMute(track.id)}
            title="静音"
          >
            M
          </button>
          <button
            className={`btn-toggle ${track.solo ? "active-warn" : ""}`}
            onClick={() => toggleSolo(track.id)}
            title="独听"
          >
            S
          </button>
          <button className="btn-toggle" onClick={() => removeTrack(track.id)} title="删除轨道">删</button>
        </div>
        <label className="gain-label">
          推子 {track.gain_db.toFixed(1)} dB
          <input
            type="range" min={-24} max={12} step={0.5}
            value={track.gain_db}
            onChange={(e) => setTrackGain(track.id, Number(e.target.value))}
          />
        </label>
        <div className="automation-controls">
          <button onClick={() => promptTrackPoint("volume")}>音量点</button>
          <button onClick={() => promptTrackPoint("pan")}>声像点</button>
          <button onClick={promptAddEffect}>加效果</button>
        </div>
        <div className="automation-summary">
          轨道点：V {track.volume_points.length} / P {track.pan_points.length}
          {track.effects.map((effect, index) => (
            <div key={index} className="effect-line">
              {effect.type} · {effect.amount_points?.length ?? 0} 强度点
              <button onClick={() => promptEffectPoint(index)}>+</button>
            </div>
          ))}
        </div>
      </div>
      <div className="track-lane" style={{ width: timelineWidth }}>
        <div className="effects-slot">
          {track.effects.length === 0
            ? "片段内自动化随片段移动；轨道自动化固定在工程时间"
            : "轨道效果链：" + track.effects.map((effect) => effect.type).join(" → ")}
        </div>
        {track.clips.map((clip) => (
          <ClipView
            key={clip.id}
            clip={clip}
            pixelsPerSecond={pixelsPerSecond}
            selected={selected?.clipId === clip.id}
            onSelect={() => select(track.id, clip.id)}
            onMoveCommit={(start) => moveClip(track.id, clip.id, start)}
            onTrimLive={(patch) => trimClipLive(track.id, clip.id, patch)}
            onTrimCommit={(patch) => trimClip(track.id, clip.id, patch)}
            onFade={(kind) => addClipFade(track.id, clip.id, kind)}
            onAddPoint={(kind) => promptClipPoint(clip.id, kind)}
            onClearAutomation={() => clearClipAutomation(track.id, clip.id)}
            onRemove={() => removeClip(track.id, clip.id)}
          />
        ))}
      </div>
    </div>
  );
}
