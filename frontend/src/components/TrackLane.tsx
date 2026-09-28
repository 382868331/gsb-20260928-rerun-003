import type { Track } from "../types";
import { useStudio } from "../store";
import { ClipBlock } from "./ClipBlock";

interface Props {
  track: Track;
  anySolo: boolean;
  timelineSec: number;
}

export function TrackLane({ track, anySolo, timelineSec }: Props) {
  const { selectedAssetId, addClip, toggleMute, toggleSolo, removeTrack, renameTrack, setTrackGain } = useStudio();
  const dimmed = track.muted || (anySolo && !track.solo);

  return (
    <div className={`track ${dimmed ? "dimmed" : ""}`} data-testid="track">
      <div className="track-controls">
        <input
          className="track-name"
          value={track.name}
          onChange={(e) => renameTrack(track.id, e.target.value)}
        />
        <div className="track-buttons">
          <button
            className={track.muted ? "active mute" : "mute"}
            data-testid="mute-btn"
            onClick={() => toggleMute(track.id)}
          >
            M
          </button>
          <button
            className={track.solo ? "active solo" : "solo"}
            data-testid="solo-btn"
            onClick={() => toggleSolo(track.id)}
          >
            S
          </button>
          <button className="danger" onClick={() => removeTrack(track.id)} title="删除轨道">
            删
          </button>
        </div>
        <label className="gain">
          音量 {(track.gain * 100).toFixed(0)}%
          <input
            type="range"
            min={0}
            max={2}
            step={0.01}
            value={track.gain}
            onChange={(e) => setTrackGain(track.id, Number(e.target.value))}
          />
        </label>
      </div>
      <div className="lane" style={{ minWidth: timelineSec * 100 }}>
        {track.clips.map((c) => (
          <ClipBlock key={c.id} track={track} clip={c} />
        ))}
      </div>
      <button
        className="add-clip"
        disabled={!selectedAssetId}
        onClick={() => selectedAssetId && addClip(track.id, selectedAssetId)}
        title={selectedAssetId ? "把选中素材放到轨道末尾" : "请先上传素材"}
      >
        + 素材
      </button>
    </div>
  );
}
