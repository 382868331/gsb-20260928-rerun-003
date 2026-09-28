export interface Clip {
  id: string;
  asset_id: string;
  offset: number;
  start: number;
  duration: number;
  gain: number;
}

export interface Track {
  id: string;
  name: string;
  clips: Clip[];
  muted: boolean;
  solo: boolean;
  gain: number;
}

export interface Project {
  version: number;
  id: string;
  name: string;
  sample_rate: number;
  tracks: Track[];
  asset_ids: string[];
}

export interface AssetMeta {
  id: string;
  original_name: string;
  sample_rate: number;
  source_sample_rate: number;
  channels: number;
  duration: number;
  peaks: number[];
}

export interface RenderResult {
  duration: number;
  peak: number;
  clipped: boolean;
  channels: number;
  warnings: string[];
}

export interface RenderStatus {
  id: string;
  status: "running" | "done" | "cancelled" | "error";
  progress: number;
  output_path: string;
  result: RenderResult | null;
  error: string | null;
}
