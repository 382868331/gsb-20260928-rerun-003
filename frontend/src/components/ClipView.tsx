import { useRef } from "react";
import type { ClipData } from "../types";

interface Props {
  clip: ClipData;
  pixelsPerSecond: number;
  selected: boolean;
  onSelect: () => void;
  onMoveCommit: (start: number) => void;
  onTrimLive: (patch: { inPoint?: number; outPoint?: number }) => void;
  onTrimCommit: (patch: { in_point?: number; out_point?: number; start?: number }) => void;
  onFade: (kind: "in" | "out") => void;
  onAddPoint: (kind: "volume" | "pan") => void;
  onClearAutomation: () => void;
  onRemove: () => void;
}

type DragMode = "move" | "left" | "right";

export function ClipView({
  clip, pixelsPerSecond, selected, onSelect, onMoveCommit, onTrimLive, onTrimCommit, onFade, onAddPoint, onClearAutomation, onRemove,
}: Props) {
  const drag = useRef<{ mode: DragMode; x: number; start: number; inPoint: number; outPoint: number; target: HTMLElement } | null>(null);

  const duration = clip.out_point - clip.in_point;
  const left = clip.start * pixelsPerSecond;
  const width = Math.max(24, duration * pixelsPerSecond);

  function begin(mode: DragMode, event: React.PointerEvent) {
    event.stopPropagation();
    event.preventDefault();
    onSelect();
    const target = event.currentTarget as HTMLElement;
    target.setPointerCapture(event.pointerId);
    drag.current = { mode, x: event.clientX, start: clip.start, inPoint: clip.in_point, outPoint: clip.out_point, target };
  }

  function handleMove(event: React.PointerEvent) {
    const state = drag.current;
    if (!state) return;
    const deltaSec = (event.clientX - state.x) / pixelsPerSecond;
    if (state.mode === "left") {
      onTrimLive({
        inPoint: Math.max(0, Math.min(state.inPoint + deltaSec, state.outPoint)),
      });
    } else if (state.mode === "right") {
      onTrimLive({ outPoint: Math.max(0, state.outPoint + deltaSec) });
    }
  }

  function handleUp(event: React.PointerEvent) {
    const state = drag.current;
    if (!state) return;
    drag.current = null;
    state.target.releasePointerCapture(event.pointerId);
    const deltaSec = (event.clientX - state.x) / pixelsPerSecond;
    if (state.mode === "move") {
      onMoveCommit(Math.max(0, state.start + deltaSec));
    } else if (state.mode === "left") {
      const inPoint = Math.max(0, Math.min(state.inPoint + deltaSec, state.outPoint));
      onTrimCommit({ in_point: inPoint, start: Math.max(0, state.start + (inPoint - state.inPoint)) });
    } else {
      onTrimCommit({ out_point: Math.max(state.inPoint, state.outPoint + deltaSec) });
    }
  }

  return (
    <div
      className={`clip ${selected ? "clip-selected" : ""}`}
      style={{ left, width }}
      onPointerDown={(e) => begin("move", e)}
      onPointerMove={handleMove}
      onPointerUp={handleUp}
      title="拖动中部移动；拖动左右边缘裁切（不修改源文件）"
    >
      <div className="clip-handle clip-handle-left" onPointerDown={(e) => begin("left", e)} />
      <div className="clip-body">
        <span className="clip-label">{clip.source.split(".")[0]}</span>
        <span className="clip-time">{clip.in_point.toFixed(2)}s–{clip.out_point.toFixed(2)}s</span>
        <span className="clip-auto">
          音量点 {clip.volume_points.length} · 声像点 {clip.pan_points.length}
        </span>
        {selected && (
          <div className="clip-actions" onPointerDown={(e) => e.stopPropagation()}>
            <button onClick={(e) => { e.stopPropagation(); onFade("in"); }}>淡入</button>
            <button onClick={(e) => { e.stopPropagation(); onFade("out"); }}>淡出</button>
            <button onClick={(e) => { e.stopPropagation(); onAddPoint("volume"); }}>量点</button>
            <button onClick={(e) => { e.stopPropagation(); onAddPoint("pan"); }}>像点</button>
            <button onClick={(e) => { e.stopPropagation(); onClearAutomation(); }}>清</button>
            <button onClick={(e) => { e.stopPropagation(); onRemove(); }} title="从时间线移除（源文件保留）">×</button>
          </div>
        )}
      </div>
      <div className="clip-handle clip-handle-right" onPointerDown={(e) => begin("right", e)} />
    </div>
  );
}
