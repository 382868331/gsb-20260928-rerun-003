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
  /** 可听度下限（dB）：该角色控制期间受控轨不允许被压到它之下；null = 不约束。 */
  floor_db?: number | null;
  /** 允许的恢复时长（秒）：停声后受控轨须在此时间内恢复；null = 不约束。 */
  max_recovery_sec?: number | null;
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
  /** 已批准的候选包络时间段；未批准段保持当前方案不变。 */
  approvals?: DuckingApproval[];
}

export interface DuckingApproval {
  plan_id: string;
  candidate: string;
  target_id: string;
  start: number;
  end: number;
}

export interface PlannerSegment {
  target_id: string;
  start: number;
  end: number;
  active_roles: string[];
  overlap: boolean;
}

export interface PlannerRecovery {
  after: number;
  recovered_at: number | null;
  duration: number;
}

export interface PlannerCandidateTarget {
  target_id: string;
  peak: number;
  min_gain_db: number;
  recoveries: PlannerRecovery[];
  max_recovery_sec: number | null;
  recovery_ok: boolean | null;
}

export interface PlannerCandidate {
  id: string;
  name: string;
  targets: PlannerCandidateTarget[];
}

export interface PlannerConflict {
  type: "audibility" | "ring";
  target_id?: string;
  start?: number;
  end?: number;
  roles?: string[];
  message: string;
}

export interface PlannerResult {
  plan_id: string;
  segments: PlannerSegment[];
  candidates: PlannerCandidate[];
  conflicts: PlannerConflict[];
}

export interface DuckingTargetReport {
  track_id: string;
  /** 检测时刻该受控轨的可读名；旧检测快照保留当时名称，改名只影响新检测。 */
  track_name?: string;
  audible: boolean;
  peak: number;
  min_gain: number;
  ranges: DuckingHandoverRange[];
}

export interface DuckingHandoverRange {
  start: number;
  end: number;
  /** 检测时刻的可读控制角色名（共同作用时以 + 连接）。 */
  control: string | null;
  /** 产生该交接区间的控制角色 id（共同作用时可有多个）。 */
  control_role_ids?: string[];
  /** 该区间内最深增益（负数 dB，如 -12.0）。 */
  max_reduction_db?: number;
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
