import { useStudio } from "../store";

export function AssetBin() {
  const { assets, selectedAssetId, selectAsset } = useStudio();
  const list = Object.values(assets);
  return (
    <aside className="asset-bin">
      <h3>素材库</h3>
      {list.length === 0 && <p className="hint">导入音频后显示在这里。所有素材导入时统一到工程采样率。</p>}
      <ul>
        {list.map((a) => (
          <li
            key={a.id}
            className={a.id === selectedAssetId ? "selected" : ""}
            onClick={() => selectAsset(a.id)}
            data-testid="asset-item"
            title={`${a.original_name}｜${a.source_sample_rate}→${a.sample_rate} Hz｜${a.duration.toFixed(2)}s｜${a.channels}ch`}
          >
            <span className="asset-name">{a.original_name}</span>
            <span className="asset-meta">
              {a.source_sample_rate === a.sample_rate
                ? `${a.sample_rate}Hz`
                : `${a.source_sample_rate}→${a.sample_rate}Hz`}
              ·{a.duration.toFixed(1)}s·{a.channels}ch
            </span>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function sourceSampleRateLabel(a: { source_sample_rate: number; sample_rate: number }): string {
  return `${a.source_sample_rate}→${a.sample_rate}Hz`;
}
