import { useMemo } from "react";
import { useStudio } from "../store";
import { TrackRow } from "./TrackRow";

const PIXELS_PER_SECOND = 80;
const MIN_WIDTH = 1200;

export function Timeline() {
  const { project, addTrack } = useStudio();
  const anySolo = project.tracks.some((t) => t.solo);

  const { width, duration } = useMemo(() => {
    const end = project.tracks.reduce((maxEnd, track) => {
      for (const clip of track.clips) {
        maxEnd = Math.max(maxEnd, clip.start + clip.out_point - clip.in_point);
      }
      return maxEnd;
    }, 10);
    return { duration: end, width: Math.max(MIN_WIDTH, Math.ceil(end * PIXELS_PER_SECOND) + 100) };
  }, [project.tracks]);

  const seconds = Math.ceil(duration);
  const ticks = Array.from({ length: seconds + 1 }, (_, i) => i);

  return (
    <div className="timeline">
      <div className="timeline-inner">
        <div className="ruler-spacer" />
        <div className="ruler" style={{ width }}>
          {ticks.map((sec) => (
            <div key={sec} className="ruler-tick" style={{ left: sec * PIXELS_PER_SECOND }}>
              <span>{sec}s</span>
            </div>
          ))}
        </div>
        {project.tracks.map((track) => (
          <TrackRow
            key={track.id}
            track={track}
            pixelsPerSecond={PIXELS_PER_SECOND}
            timelineWidth={width}
            anySolo={anySolo}
          />
        ))}
        <button className="add-track" onClick={addTrack}>+ 添加轨道</button>
      </div>
    </div>
  );
}
