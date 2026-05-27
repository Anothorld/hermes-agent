import type { RunKind } from '../agentRunStyles';

export type SessionRun = {
  run_id: string;
  kind: RunKind;
  started_at: string;
  ended_at: string | null;
};

export type AgentSession = {
  session_id: string;
  campaign_id: string;
  kinds: RunKind[];
  runs: SessionRun[];
  first_started_at: string;
  last_activity_at: string;
  open: boolean;
};

export type AgentSessionsResponse = {
  env: 'TEST' | 'LIVE';
  sessions: AgentSession[];
};
