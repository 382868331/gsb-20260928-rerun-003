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
    moveClip, trimClipLive, trimClip, removeClip,
  } = useStudio();

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
      </div>
      <div className="track-lane" style={{ width: timelineWidth }}>
        <div className="effects-slot">效果器链（预留：{track.effects.length} 个节点）</div>
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
            onRemove={() => removeClip(track.id, clip.id)}
          />
        ))}
      </div>
    </div>
  );
}
