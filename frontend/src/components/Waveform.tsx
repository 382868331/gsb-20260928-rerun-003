import type { AssetMeta } from "../types";

interface Props {
  asset: AssetMeta | undefined;
  start: number;
  duration: number;
}

/**
 * 按素材的裁切区间显示对应段的波形峰值。
 * 裁切只改变 start/duration，原始 peaks 不变。
 */
export function Waveform({ asset, start, duration }: Props) {
  const bars = 60;
  let values = new Array(bars).fill(0) as number[];
  if (asset && asset.peaks.length > 0 && duration > 0) {
    const total = asset.peaks.length;
    const i0 = Math.floor((start / asset.duration) * total);
    const i1 = Math.max(i0 + 1, Math.floor(((start + duration) / asset.duration) * total));
    const slice = asset.peaks.slice(i0, Math.min(total, i1));
    const step = slice.length / bars;
    values = values.map((_, i) => {
      const lo = Math.floor(i * step);
      const hi = Math.max(lo + 1, Math.floor((i + 1) * step));
      const part = slice.slice(lo, hi);
      return part.length ? Math.max(...part) : 0;
    });
  }
  return (
    <div className="waveform" aria-label="波形">
      {values.map((v, i) => (
        <span key={i} className="wave-bar" style={{ height: `${Math.max(4, v * 100)}%` }} />
      ))}
    </div>
  );
}
