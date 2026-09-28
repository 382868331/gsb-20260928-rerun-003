import { useRef } from "react";
import type { Clip, Track } from "../types";
import { useStudio, PX_PER_SEC } from "../store";
import { Waveform } from "./Waveform";

interface Props {
  track: Track;
  clip: Clip;
}

type DragMode = "move" | "left" | "right";

export function ClipBlock({ track, clip }: Props) {
  const { assets, moveClip, trimClip, removeClip } = useStudio();
  const drag = useRef<{ mode: DragMode; x: number; orig: Clip } | null>(null);
  const asset = assets[clip.asset_id];

  const begin = (mode: DragMode) => (e: React.PointerEvent) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    drag.current = { mode, x: e.clientX, orig: { ...clip } };
  };

  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dt = (e.clientX - d.x) / PX_PER_SEC;
    if (d.mode === "move") {
      moveClip(track.id, clip.id, d.orig.offset + dt);
    } else if (d.mode === "left") {
      trimClip(track.id, clip.id, dt, 0);
    } else {
      trimClip(track.id, clip.id, 0, dt);
    }
  };

  const end = () => {
    drag.current = null;
  };

  return (
    <div
      className="clip"
      data-testid="clip"
      style={{ left: clip.offset * PX_PER_SEC, width: clip.duration * PX_PER_SEC }}
      onPointerDown={begin("move")}
      onPointerMove={onMove}
      onPointerUp={end}
      title={`${asset?.original_name ?? clip.asset_id}（位置 ${clip.offset.toFixed(2)}s，裁切 ${clip.start.toFixed(2)}+${clip.duration.toFixed(2)}s）`}
    >
      <div className="clip-handle left" onPointerDown={begin("left")} data-testid="trim-left" />
      <div className="clip-body">
        <div className="clip-label">
          {asset?.original_name ?? "素材"}
          <button className="clip-del" data-testid="clip-delete" onClick={() => removeClip(track.id, clip.id)}>
            ×
          </button>
        </div>
        <Waveform asset={asset} start={clip.start} duration={clip.duration} />
      </div>
      <div className="clip-handle right" onPointerDown={begin("right")} data-testid="trim-right" />
    </div>
  );
}
