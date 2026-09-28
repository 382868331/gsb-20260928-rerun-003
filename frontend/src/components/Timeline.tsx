import { useStudio, PX_PER_SEC } from "../store";
import { TrackLane } from "./TrackLane";

export function Timeline() {
  const { project } = useStudio();
  const anySolo = project.tracks.some((t) => t.solo);
  const contentEnd = project.tracks.reduce(
    (m, t) => Math.max(m, ...t.clips.map((c) => c.offset + c.duration), 0),
    0,
  );
  const timelineSec = Math.max(6, Math.ceil(contentEnd + 1));
  const ticks = Array.from({ length: Math.floor(timelineSec) + 1 }, (_, i) => i);

  return (
    <main className="timeline-wrap">
      <div className="ruler" style={{ minWidth: timelineSec * PX_PER_SEC }}>
        {ticks.map((i) => (
          <span key={i} className="tick" style={{ left: i * PX_PER_SEC }}>
            {i}s
          </span>
        ))}
      </div>
      {project.tracks.length === 0 && <p className="hint empty">还没有轨道，点「新增轨道」开始。</p>}
      {project.tracks.map((t) => (
        <TrackLane key={t.id} track={t} anySolo={anySolo} timelineSec={timelineSec} />
      ))}
    </main>
  );
}
