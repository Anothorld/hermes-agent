import { api } from '../api';

type LaunchJobRow = {
  status?: string;
  result?: {
    run_id?: string | null;
    ok?: boolean;
    queued?: boolean;
    waited_sec?: number;
    queue_position?: number;
  };
  error?: string;
};

export type LaunchJobOutcome = {
  run_id: string | null;
  queued?: boolean;
  waited_sec?: number;
  queue_position?: number;
};

/** True when POST returned HTTP 202 launch accept body. */
export function isAsyncLaunchJob(
  out: Record<string, unknown>,
): out is { job_id: string; status: 'accepted'; poll?: string } {
  return typeof out.job_id === 'string' && out.status === 'accepted';
}

/** Poll ``GET /campaigns/launch-jobs/{id}`` until gateway run is known. */
export async function pollLaunchJob(
  jobId: string,
  opts?: { intervalMs?: number; timeoutMs?: number },
): Promise<LaunchJobOutcome> {
  const intervalMs = opts?.intervalMs ?? 2000;
  const timeoutMs = opts?.timeoutMs ?? 600_000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const row = await api.get<LaunchJobRow>(
      `/campaigns/launch-jobs/${encodeURIComponent(jobId)}`,
    );
    if (row.status === 'completed') {
      const result = row.result ?? {};
      return {
        run_id:
          typeof result.run_id === 'string' ? result.run_id : null,
        queued: result.queued,
        waited_sec: result.waited_sec,
        queue_position: result.queue_position,
      };
    }
    if (row.status === 'failed') {
      throw new Error(row.error || 'Agent 启动失败，请稍后重试');
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('启动仍在排队中，请稍后在 Agent 浮层查看进度');
}

/** Resolve run id from sync or async launch response. */
export async function resolveLaunchRunId(
  out: Record<string, unknown>,
  pendingRunId?: string | null,
): Promise<string | null> {
  if (isAsyncLaunchJob(out)) {
    const done = await pollLaunchJob(out.job_id);
    return done.run_id ?? pendingRunId ?? null;
  }
  const syncRun = out.run_id;
  return (
    (typeof syncRun === 'string' ? syncRun : null)
    ?? pendingRunId
    ?? null
  );
}
