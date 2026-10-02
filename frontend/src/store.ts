import { create } from "zustand";
import type { AutomationPointData, ClipData, EffectData, ProjectData, TrackData } from "./types";

const uid = () => Math.random().toString(36).slice(2, 10);

const defaultProfileA = {
  threshold_db: -30,
  reduction_db: 12,
  attack_sec: 0.08,
  hold_sec: 0.18,
  release_sec: 0.65,
};

const defaultProfileB = {
  threshold_db: -24,
  reduction_db: 18,
  attack_sec: 0.04,
  hold_sec: 0.3,
  release_sec: 1.2,
};

function normalizeProject(project: ProjectData): ProjectData {
  return {
    ...project,
    active_ducking: project.active_ducking ?? "none",
    ducking_profiles: {
      A: { ...defaultProfileA, ...project.ducking_profiles?.A },
      B: { ...defaultProfileB, ...project.ducking_profiles?.B },
    },
    tracks: project.tracks.map((track) => ({
      ...track,
      ducking_role: track.ducking_role ?? "normal",
    })),
  };
}

export const emptyProject: ProjectData = {
  format: "audio-studio-project",
  version: 1,
  name: "未命名工程",
  sample_rate: 44100,
  channels: 2,
  master_gain_db: 0,
  active_ducking: "none",
  ducking_profiles: { A: { ...defaultProfileA }, B: { ...defaultProfileB } },
  tracks: [],
};

type TrimPatch = Partial<Pick<ClipData, "in_point" | "out_point" | "start">>;
type LiveTrimPatch = { inPoint?: number; outPoint?: number };

interface StudioState {
  project: ProjectData;
  selected: { trackId: string; clipId: string } | null;
  mediaDurations: Record<string, number>;
  setProject: (project: ProjectData) => void;
  setTrackDuckingRole: (trackId: string, role: TrackData["ducking_role"]) => void;
  setActiveDucking: (active: ProjectData["active_ducking"]) => void;
  updateDuckingProfile: (name: "A" | "B", patch: Partial<ProjectData["ducking_profiles"]["A"]>) => void;
  select: (trackId: string, clipId: string | null) => void;
  addTrack: () => void;
  removeTrack: (trackId: string) => void;
  toggleMute: (trackId: string) => void;
  toggleSolo: (trackId: string) => void;
  setTrackGain: (trackId: string, gainDb: number) => void;
  setMasterGain: (gainDb: number) => void;
  addClip: (trackId: string, source: string, duration: number) => void;
  moveClip: (trackId: string, clipId: string, start: number) => void;
  trimClip: (trackId: string, clipId: string, patch: TrimPatch) => void;
  trimClipLive: (trackId: string, clipId: string, patch: LiveTrimPatch) => void;
  setClipGain: (trackId: string, clipId: string, gainDb: number) => void;
  setClipFades: (trackId: string, clipId: string, fadeIn: number, fadeOut: number) => void;
  setClipAutomation: (trackId: string, clipId: string, points: AutomationPointData[]) => void;
  setTrackPan: (trackId: string, pan: number) => void;
  setTrackAutomation: (trackId: string, points: AutomationPointData[]) => void;
  addEffect: (trackId: string, effect: EffectData) => void;
  updateEffect: (trackId: string, index: number, patch: Partial<EffectData>) => void;
  removeEffect: (trackId: string, index: number) => void;
  removeClip: (trackId: string, clipId: string) => void;
  rememberMedia: (source: string, duration: number) => void;
}

function mutateTrack(project: ProjectData, trackId: string, fn: (track: TrackData) => void): ProjectData {
  return {
    ...project,
    tracks: project.tracks.map((track) => {
      if (track.id !== trackId) return track;
      const next: TrackData = {
        ...track,
        clips: track.clips.map((clip) => ({ ...clip, automation: [...clip.automation] })),
        effects: track.effects.map((effect) => ({ ...effect })),
        automation: [...track.automation],
        ducking_role: track.ducking_role ?? "normal",
      };
      fn(next);
      return next;
    }),
  };
}

function withClip(
  state: StudioState,
  trackId: string,
  clipId: string,
  fn: (clip: ClipData, track: TrackData, sourceDuration: number | undefined) => void,
): ProjectData {
  return mutateTrack(state.project, trackId, (track) => {
    const clip = track.clips.find((item) => item.id === clipId);
    if (clip) fn(clip, track, state.mediaDurations[clip.source]);
  });
}

export const useStudio = create<StudioState>((set) => ({
  project: emptyProject,
  selected: null,
  mediaDurations: {},

  setProject: (project) => set({ project: normalizeProject(project), selected: null }),

  setTrackDuckingRole: (trackId, role) =>
    set((state) => ({ project: mutateTrack(state.project, trackId, (track) => { track.ducking_role = role; }) })),

  setActiveDucking: (active) =>
    set((state) => ({ project: { ...state.project, active_ducking: active } })),

  updateDuckingProfile: (name, patch) =>
    set((state) => ({
      project: {
        ...state.project,
        ducking_profiles: {
          ...state.project.ducking_profiles,
          [name]: { ...state.project.ducking_profiles[name], ...patch },
        },
      },
    })),

  select: (trackId, clipId) =>
    set({ selected: clipId === null ? null : { trackId, clipId } }),

  addTrack: () =>
    set((state) => ({
      project: {
        ...state.project,
        tracks: [
          ...state.project.tracks,
          {
            id: uid(),
            name: `轨道 ${state.project.tracks.length + 1}`,
            muted: false,
            solo: false,
            gain_db: 0,
            pan: 0,
            effects: [],
            automation: [],
            ducking_role: "normal",
            clips: [],
          },
        ],
      },
    })),

  removeTrack: (trackId) =>
    set((state) => ({
      project: { ...state.project, tracks: state.project.tracks.filter((t) => t.id !== trackId) },
      selected: state.selected?.trackId === trackId ? null : state.selected,
    })),

  toggleMute: (trackId) =>
    set((state) => ({ project: mutateTrack(state.project, trackId, (t) => { t.muted = !t.muted; }) })),

  toggleSolo: (trackId) =>
    set((state) => ({ project: mutateTrack(state.project, trackId, (t) => { t.solo = !t.solo; }) })),

  setTrackGain: (trackId, gainDb) =>
    set((state) => ({ project: mutateTrack(state.project, trackId, (t) => { t.gain_db = gainDb; }) })),

  setMasterGain: (gainDb) =>
    set((state) => ({ project: { ...state.project, master_gain_db: gainDb } })),

  addClip: (trackId, source, duration) =>
    set((state) => {
      const lastEnd = state.project.tracks
        .flatMap((t) => t.clips)
        .reduce((maxEnd, clip) => Math.max(maxEnd, clip.start + clip.out_point - clip.in_point), 0);
      const clip: ClipData = {
        id: uid(),
        source,
        start: lastEnd,
        in_point: 0,
        out_point: duration,
        gain_db: 0,
        automation: [],
      };
      const project = mutateTrack(state.project, trackId, (t) => t.clips.push({ ...clip }));
      return { project, selected: { trackId, clipId: clip.id } };
    }),

  moveClip: (trackId, clipId, start) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip) => {
        clip.start = Math.max(0, Number(start.toFixed(4)));
      }),
    })),

  trimClip: (trackId, clipId, patch) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip, _track, duration) => {
        if (patch.in_point !== undefined) {
          clip.in_point = Math.min(Math.max(0, patch.in_point), clip.out_point);
        }
        if (patch.out_point !== undefined) {
          clip.out_point = Math.max(clip.in_point, patch.out_point);
        }
        if (patch.start !== undefined) {
          clip.start = Math.max(0, patch.start);
        }
        if (duration !== undefined) {
          clip.in_point = Math.min(clip.in_point, duration);
          clip.out_point = Math.min(clip.out_point, duration);
        }
      }),
    })),

  trimClipLive: (trackId, clipId, patch) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip, _track, duration) => {
        if (patch.inPoint !== undefined) {
          const inPoint = Math.max(0, Math.min(patch.inPoint, clip.out_point));
          clip.start = Math.max(0, clip.start + (inPoint - clip.in_point));
          clip.in_point = inPoint;
        }
        if (patch.outPoint !== undefined) {
          clip.out_point = Math.max(clip.in_point, patch.outPoint);
        }
        if (duration !== undefined) {
          clip.in_point = Math.min(clip.in_point, duration);
          clip.out_point = Math.min(clip.out_point, duration);
        }
      }),
    })),

  setClipGain: (trackId, clipId, gainDb) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip) => {
        clip.gain_db = gainDb;
      }),
    })),

  setClipFades: (trackId, clipId, fadeIn, fadeOut) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip) => {
        const duration = clip.out_point - clip.in_point;
        const points = clip.automation.filter((p) => p.time !== 0 && Math.abs(p.time - duration) > 1e-9);
        const next: AutomationPointData[] = [...points];
        if (fadeIn > 0) {
          next.push({ time: 0, gain_db: -120 });
          next.push({ time: Math.min(fadeIn, duration), gain_db: 0 });
        }
        if (fadeOut > 0) {
          next.push({ time: Math.max(0, duration - fadeOut), gain_db: 0 });
          next.push({ time: duration, gain_db: -120 });
        }
        next.sort((a, b) => a.time - b.time);
        clip.automation = next;
      }),
    })),

  setClipAutomation: (trackId, clipId, points) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip) => {
        clip.automation = [...points].sort((a, b) => a.time - b.time);
      }),
    })),

  setTrackPan: (trackId, pan) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (t) => { t.pan = Math.max(-1, Math.min(1, pan)); }),
    })),

  setTrackAutomation: (trackId, points) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (t) => {
        t.automation = [...points].sort((a, b) => a.time - b.time);
      }),
    })),

  addEffect: (trackId, effect) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (t) => { t.effects.push({ amount: 1, ...effect }); }),
    })),

  updateEffect: (trackId, index, patch) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (t) => {
        t.effects[index] = { ...t.effects[index], ...patch };
      }),
    })),

  removeEffect: (trackId, index) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (t) => { t.effects.splice(index, 1); }),
    })),

  removeClip: (trackId, clipId) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (t) => {
        t.clips = t.clips.filter((c) => c.id !== clipId);
      }),
      selected: state.selected?.clipId === clipId ? null : state.selected,
    })),

  rememberMedia: (source, duration) =>
    set((state) => ({ mediaDurations: { ...state.mediaDurations, [source]: duration } })),
}));
