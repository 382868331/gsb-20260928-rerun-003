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
  clips: ClipData[];
}

export interface DuckingPreset {
  id: "a" | "b";
  name: string;
  threshold_db: number;
  depth_db: number;
  attack_sec: number;
  hold_sec: number;
  release_sec: number;
}

export interface DuckingConfig {
  control_track_id: string | null;
  target_track_ids: string[];
  active_preset_id: "off" | "a" | "b";
  presets: DuckingPreset[];
}

export interface DuckingRole {
  id: string;
  name: string;
  priority: number;
  control_track_ids: string[];
  target_track_ids: string[];
  /** pre = 用受控前的原始声音做电平分析；post = 用被其他角色压低后的声音。 */
  source: "pre" | "post";
  threshold_db: number;
  depth_db: number;
  attack_sec: number;
  hold_sec: number;
  release_sec: number;
}

export interface DuckingPlan {
  id: string;
  name: string;
  /** strongest = 较强一方逐采样主导；multiply = 增益相乘共同作用。 */
  combine: "strongest" | "multiply";
  roles: DuckingRole[];
}

export interface DuckingConfigV2 {
  version: 2;
  active_plan_id: string;
  plans: DuckingPlan[];
}

export interface DuckingTargetReport {
  track_id: string;
  audible: boolean;
  peak: number;
  min_gain: number;
  ranges: { start: number; end: number; control: string | null; control_ids?: string[] }[];
}

export interface DuckingReport {
  plan_id: string;
  combine: "strongest" | "multiply";
  roles: { id: string; name: string; priority: number; depth_db: number;
           source: "pre" | "post"; control_track_ids: string[];
           target_track_ids: string[] }[];
  targets: DuckingTargetReport[];
}

export interface ProjectData {
  format: string;
  version: number;
  name: string;
  sample_rate: number;
  channels: number;
  master_gain_db: number;
  tracks: TrackData[];
  ducking?: DuckingConfig | DuckingConfigV2 | null;
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
  ducking_ranges: { start: number; end: number }[];
  ducking_report?: DuckingReport | null;
}
