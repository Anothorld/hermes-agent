import { api } from '../api';

export type NoxBatchJobResult = {
  processed_count?: number;
  error_count?: number;
  processed?: unknown[];
  errors?: unknown[];
  identity_ids?: number[];
};

type JobRow = {
  status?: string;
  result?: NoxBatchJobResult;
  error?: string;
};

/** True when POST returned HTTP 202 Nox batch accept body. */
export function isAsyncNoxBatchJob(
  out: Record<string, unknown>,
): out is { job_id: string; status: 'accepted'; poll?: string; identity_count?: number } {
  return typeof out.job_id === 'string' && out.status === 'accepted';
}

/** Poll ``GET /kols/jobs/{id}`` until batch diligence finishes (default 10 min). */
export async function pollNoxBatchJob(
  jobId: string,
  opts?: { intervalMs?: number; timeoutMs?: number },
): Promise<NoxBatchJobResult> {
  const intervalMs = opts?.intervalMs ?? 2000;
  const timeoutMs = opts?.timeoutMs ?? 600_000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const row = await api.get<JobRow>(`/kols/jobs/${encodeURIComponent(jobId)}`);
    if (row.status === 'completed') {
      return row.result ?? {};
    }
    if (row.status === 'failed') {
      throw new Error(row.error || 'Nox 批量尽调失败');
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('Nox 批量尽调仍在后台运行，请稍后刷新 shortlist 查看结果');
}
