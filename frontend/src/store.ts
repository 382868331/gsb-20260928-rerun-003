import { create } from "zustand";
import type { AutomationPointData, ClipData, EffectData, ProjectData, TrackData } from "./types";

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

function upsertPoint(points: AutomationPointData[], point: AutomationPointData): AutomationPointData[] {
  return [...points.filter((item) => item.time !== point.time), point].sort((a, b) => a.time - b.time);
}

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
  addClip: (trackId: string, source: string, duration: number) => void;
  moveClip: (trackId: string, clipId: string, start: number) => void;
  trimClip: (trackId: string, clipId: string, patch: TrimPatch) => void;
  trimClipLive: (trackId: string, clipId: string, patch: LiveTrimPatch) => void;
  setClipGain: (trackId: string, clipId: string, gainDb: number) => void;
  addClipFade: (trackId: string, clipId: string, kind: "in" | "out") => void;
  addClipPoint: (trackId: string, clipId: string, kind: "volume" | "pan", time: number, value: number) => void;
  clearClipAutomation: (trackId: string, clipId: string) => void;
  addTrackPoint: (trackId: string, kind: "volume" | "pan", time: number, value: number) => void;
  addEffect: (trackId: string, effect: EffectData) => void;
  addEffectPoint: (trackId: string, effectIndex: number, time: number, value: number) => void;
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
        effects: track.effects.map((effect) => ({ ...effect, amount_points: [...(effect.amount_points ?? [])] })),
        volume_points: [...track.volume_points],
        pan_points: [...track.pan_points],
        clips: track.clips.map((clip) => ({
          ...clip,
          volume_points: [...clip.volume_points],
          pan_points: [...clip.pan_points],
        })),
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
            effects: [],
            volume_points: [],
            pan_points: [],
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
        volume_points: [],
        pan_points: [],
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

  addClipFade: (trackId, clipId, kind) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip) => {
        const duration = clip.out_point - clip.in_point;
        const fadeEnd = Math.min(0.25, duration);
        clip.volume_points = kind === "in"
          ? [{ time: 0, value: 0 }, { time: fadeEnd, value: 1 }]
          : [{ time: 0, value: 1 }, { time: duration - fadeEnd, value: 1 }, { time: duration, value: 0 }];
      }),
    })),

  addClipPoint: (trackId, clipId, kind, time, value) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip) => {
        const point: AutomationPointData = {
          time: Math.max(0, Number(time.toFixed(3))),
          value: Number(value.toFixed(3)),
        };
        if (kind === "volume") clip.volume_points = upsertPoint(clip.volume_points, point);
        else clip.pan_points = upsertPoint(clip.pan_points, point);
      }),
    })),

  clearClipAutomation: (trackId, clipId) =>
    set((state) => ({
      project: withClip(state, trackId, clipId, (clip) => {
        clip.volume_points = [];
        clip.pan_points = [];
      }),
    })),

  addTrackPoint: (trackId, kind, time, value) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (track) => {
        const point: AutomationPointData = { time: Math.max(0, Number(time.toFixed(3))), value: Number(value.toFixed(3)) };
        if (kind === "volume") track.volume_points = upsertPoint(track.volume_points, point);
        else track.pan_points = upsertPoint(track.pan_points, point);
      }),
    })),

  addEffect: (trackId, effect) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (track) => {
        track.effects.push(effect);
      }),
    })),

  addEffectPoint: (trackId, effectIndex, time, value) =>
    set((state) => ({
      project: mutateTrack(state.project, trackId, (track) => {
        const effect = track.effects[effectIndex];
        if (!effect) return;
        const point: AutomationPointData = {
          time: Math.max(0, Number(time.toFixed(3))),
          value: Math.min(1, Math.max(0, Number(value.toFixed(3)))),
        };
        effect.amount_points = upsertPoint(effect.amount_points ?? [], point);
      }),
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
