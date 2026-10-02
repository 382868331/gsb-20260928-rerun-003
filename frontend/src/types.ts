export interface ClipData {
  id: string;
  source: string;
  start: number;
  in_point: number;
  out_point: number;
  gain_db: number;
  automation: AutomationPointData[];
}

export interface EffectData {
  type: "gain" | "delay" | string;
  gain_db?: number;
  delay_sec?: number;
  feedback?: number;
  wet?: number;
  amount?: number;
  [key: string]: unknown;
}

export interface AutomationPointData {
  time: number;
  gain_db?: number | null;
  pan?: number | null;
  amount?: number | null;
}

export interface TrackData {
  id: string;
  name: string;
  muted: boolean;
  solo: boolean;
  gain_db: number;
  pan: number;
  effects: EffectData[];
  automation: AutomationPointData[];
  ducking_role: "normal" | "control" | "music";
  clips: ClipData[];
}

export interface DuckingProfile {
  threshold_db: number;
  reduction_db: number;
  attack_sec: number;
  hold_sec: number;
  release_sec: number;
}

export interface ProjectData {
  format: string;
  version: number;
  name: string;
  sample_rate: number;
  channels: number;
  master_gain_db: number;
  active_ducking: "none" | "A" | "B";
  ducking_profiles: { A: DuckingProfile; B: DuckingProfile };
  tracks: TrackData[];
}

export interface MediaInfo {
  source: string;
  original_name: string;
  sample_rate: number;
  channels: number;
  duration: number;
}

export interface PreviewReport {
  duration: number;
  peak: number;
  peak_before_master: number;
  over_range: boolean;
  over_count: number;
  over_ranges: { start: number; end: number }[];
  duck_ranges?: { start: number; end: number }[];
}
