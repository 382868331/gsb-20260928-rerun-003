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
  onRemove: () => void;
  onAutomation: (points: ClipData["automation"]) => void;
}

type DragMode = "move" | "left" | "right";

export function ClipView({
  clip, pixelsPerSecond, selected, onSelect, onMoveCommit, onTrimLive, onTrimCommit, onRemove, onAutomation,
}: Props) {
  const drag = useRef<{ mode: DragMode; x: number; start: number; inPoint: number; outPoint: number; target: HTMLElement } | null>(null);

  const duration = clip.out_point - clip.in_point;
  const left = clip.start * pixelsPerSecond;
  const width = Math.max(24, duration * pixelsPerSecond);

  function handleLaneDoubleClick(event: React.MouseEvent) {
    event.stopPropagation();
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const rel = Math.max(0, Math.min(duration, (event.clientX - rect.left) / pixelsPerSecond));
    const vertical = 1 - Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    const gainDb = Math.round((-24 + vertical * 24) * 10) / 10;
    const pan = event.shiftKey ? Math.round((vertical * 2 - 1) * 100) / 100 : null;
    const points = [...clip.automation];
    const existing = points.findIndex((p) => Math.abs(p.time - rel) < 0.02);
    const point = { time: Number(rel.toFixed(3)), gain_db: pan === null ? gainDb : null, pan };
    if (existing >= 0) points[existing] = point; else points.push(point);
    points.sort((a, b) => a.time - b.time);
    onAutomation(points);
  }

  const polyPoints = (key: "gain_db" | "pan") => {
    const pts = clip.automation.filter((p) => p[key] !== null && p[key] !== undefined);
    if (pts.length === 0) return "";
    const toY = (value: number) => key === "gain_db"
      ? (1 - (Math.max(-24, Math.min(0, value)) + 24) / 24) * 100
      : (1 - ((value + 1) / 2)) * 100;
    const sorted = [...pts].sort((a, b) => a.time - b.time);
    return sorted.map((p) => `${(p.time / duration) * 100},${toY((p[key] as number))}`).join(" ");
  };

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
        {selected && (
          <button
            className="clip-delete"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onRemove(); }}
            title="从时间线移除（源文件保留）"
          >
            ×
          </button>
        )}
      </div>
      <div
        className="clip-automation"
        onDoubleClick={handleLaneDoubleClick}
        title="双击添加音量断点（Shift+双击添加声像断点）"
      >
        {polyPoints("gain_db") && (
          <svg className="clip-auto-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            <polyline points={polyPoints("gain_db")} className="auto-line auto-gain" fill="none" />
          </svg>
        )}
        {polyPoints("pan") && (
          <svg className="clip-auto-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            <polyline points={polyPoints("pan")} className="auto-line auto-pan" fill="none" />
          </svg>
        )}
      </div>
      <div className="clip-handle clip-handle-right" onPointerDown={(e) => begin("right", e)} />
    </div>
  );
}
