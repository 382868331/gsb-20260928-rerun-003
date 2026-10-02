import { create } from "zustand";
import type { AutomationPointData, ClipData, DuckingConfig, DuckingPreset, DuckingRole, DuckingScheme, EffectData, ProjectData, TrackData } from "./types";
import { defaultDucking, defaultRole, defaultScheme } from "./api";

const uid = () => Math.random().toString(36).slice(2, 10);

export const emptyProject: ProjectData = {
  format: "audio-studio-project",
  version: 1,
  name: "未命名工程",
  sample_rate: 44100,
  channels: 2,
  master_gain_db: 0,
  tracks: [],
};

type TrimPatch = Partial<Pick<ClipData, "in_point" | "out_point" | "start">>;
type LiveTrimPatch = { inPoint?: number; outPoint?: number };

interface StudioState {
  project: ProjectData;
  selected: { trackId: string; clipId: string } | null;
  mediaDurations: Record<string, number>;
  setProject: (project: ProjectData) => void;
  select: (trackId: string, clipId: string | null) => void;
  addTrack: () => void;
  removeTrack: (trackId: string) => void;
  toggleMute: (trackId: string) => void;
  toggleSolo: (trackId: string) => void;
  setTrackGain: (trackId: string, gainDb: number) => void;
  setMasterGain: (gainDb: number) => void;
  ensureDucking: () => void;
  updateDucking: (patch: Partial<DuckingConfig>) => void;
  updateDuckingPreset: (id: "a" | "b", patch: Partial<DuckingPreset>) => void;
  toggleDuckingTarget: (trackId: string) => void;
  addScheme: () => void;
  duplicateScheme: (schemeId: string) => void;
  removeScheme: (schemeId: string) => void;
  setActiveScheme: (schemeId: string | null) => void;
  updateScheme: (schemeId: string, patch: Partial<DuckingScheme>) => void;
  addRole: (schemeId: string) => void;
  updateRole: (schemeId: string, roleId: string, patch: Partial<DuckingRole>) => void;
  removeRole: (schemeId: string, roleId: string) => void;
  toggleRoleTrack: (schemeId: string, roleId: string, key: "control_track_ids" | "target_track_ids", trackId: string) => void;
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
      };
      fn(next);
      return next;
    }),
  };
}

function mutateDucking(state: StudioState, fn: (ducking: DuckingConfig) => void): ProjectData {
  const ducking: DuckingConfig = {
    ...defaultDucking(),
    ...(state.project.ducking ?? {}),
    schemes: (state.project.ducking?.schemes ?? []).map((scheme) => ({
      ...scheme,
      roles: scheme.roles.map((role) => ({
        ...role,
        control_track_ids: [...role.control_track_ids],
        target_track_ids: [...role.target_track_ids],
      })),
    })),
    presets: (state.project.ducking?.presets ?? defaultDucking().presets).map((p) => ({ ...p })),
    target_track_ids: [...(state.project.ducking?.target_track_ids ?? [])],
  };
  fn(ducking);
  return { ...state.project, ducking };
}

function mutateScheme(state: StudioState, schemeId: string, fn: (scheme: DuckingScheme) => void): ProjectData {
  return mutateDucking(state, (ducking) => {
    const scheme = ducking.schemes.find((item) => item.id === schemeId);
    if (scheme) fn(scheme);
  });
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

  setProject: (project) => set({ project, selected: null }),

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

  ensureDucking: () =>
    set((state) => state.project.ducking
      ? {}
      : { project: { ...state.project, ducking: defaultDucking() } }),

  updateDucking: (patch) =>
    set((state) => {
      const ducking = state.project.ducking ?? defaultDucking();
      return { project: { ...state.project, ducking: { ...ducking, ...patch } } };
    }),

  updateDuckingPreset: (id, patch) =>
    set((state) => {
      const ducking = state.project.ducking ?? defaultDucking();
      return {
        project: {
          ...state.project,
          ducking: {
            ...ducking,
            presets: ducking.presets.map((preset) => preset.id === id ? { ...preset, ...patch } : preset),
          },
        },
      };
    }),

  toggleDuckingTarget: (trackId) =>
    set((state) => {
      const ducking = state.project.ducking ?? defaultDucking();
      const target_track_ids = ducking.target_track_ids.includes(trackId)
        ? ducking.target_track_ids.filter((id) => id !== trackId)
        : [...ducking.target_track_ids, trackId];
      return { project: { ...state.project, ducking: { ...ducking, target_track_ids } } };
    }),

  addScheme: () =>
    set((state) => ({
      project: mutateDucking(state, (ducking) => {
        const id = "scheme-" + uid();
        ducking.schemes.push(defaultScheme(id, "方案 " + (ducking.schemes.length + 1)));
        ducking.active_scheme_id = id;
      }),
    })),

  duplicateScheme: (schemeId) =>
    set((state) => ({
      project: mutateDucking(state, (ducking) => {
        const source = ducking.schemes.find((item) => item.id === schemeId);
        if (!source) return;
        // 副本是完全独立的快照：之后修改任一方案互不影响。
        const copy: DuckingScheme = JSON.parse(JSON.stringify(source));
        copy.id = "scheme-" + uid();
        copy.name = source.name + " 副本";
        copy.roles = copy.roles.map((role) => ({ ...role, id: "role-" + uid() }));
        ducking.schemes.push(copy);
        ducking.active_scheme_id = copy.id;
      }),
    })),

  removeScheme: (schemeId) =>
    set((state) => ({
      project: mutateDucking(state, (ducking) => {
        ducking.schemes = ducking.schemes.filter((item) => item.id !== schemeId);
        if (ducking.active_scheme_id === schemeId) ducking.active_scheme_id = null;
      }),
    })),

  setActiveScheme: (schemeId) =>
    set((state) => ({
      project: mutateDucking(state, (ducking) => {
        ducking.active_scheme_id = schemeId;
      }),
    })),

  updateScheme: (schemeId, patch) =>
    set((state) => ({
      project: mutateScheme(state, schemeId, (scheme) => Object.assign(scheme, patch)),
    })),

  addRole: (schemeId) =>
    set((state) => ({
      project: mutateScheme(state, schemeId, (scheme) => {
        scheme.roles.push(defaultRole("role-" + uid(), "角色 " + (scheme.roles.length + 1)));
      }),
    })),

  updateRole: (schemeId, roleId, patch) =>
    set((state) => ({
      project: mutateScheme(state, schemeId, (scheme) => {
        const role = scheme.roles.find((item) => item.id === roleId);
        if (role) Object.assign(role, patch);
      }),
    })),

  removeRole: (schemeId, roleId) =>
    set((state) => ({
      project: mutateScheme(state, schemeId, (scheme) => {
        scheme.roles = scheme.roles.filter((item) => item.id !== roleId);
      }),
    })),

  toggleRoleTrack: (schemeId, roleId, key, trackId) =>
    set((state) => ({
      project: mutateScheme(state, schemeId, (scheme) => {
        const role = scheme.roles.find((item) => item.id === roleId);
        if (!role) return;
        // 同一来源重复归组只保留一次，不会多压一次。
        role[key] = role[key].includes(trackId)
          ? role[key].filter((id) => id !== trackId)
          : [...role[key], trackId];
      }),
    })),

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
