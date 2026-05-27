// Shared palette / labels for the gateway run kinds used by both the
// per-campaign AgentTranscriptPanel and the global Agent Session Dock.

export type RunKind = 'outreach' | 'reply' | 'draft' | 'resume' | 'refine';

export const RUN_KIND_LABEL: Record<RunKind, string> = {
  outreach: 'outreach',
  reply: 'reply',
  draft: 'draft',
  resume: 'resume',
  refine: 'refine',
};

export const RUN_KIND_TONE: Record<RunKind, string> = {
  outreach: 'bg-emerald-900/60 text-emerald-200',
  reply: 'bg-sky-900/60 text-sky-200',
  draft: 'bg-amber-900/60 text-amber-200',
  resume: 'bg-violet-900/60 text-violet-200',
  refine: 'bg-pink-900/60 text-pink-200',
};
