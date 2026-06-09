import { api } from '../api';

type JobRow = {
  status?: string;
  result?: Record<string, unknown>;
  error?: string;
};

/** Poll async learning job until completed or failed (max 5 min). */
export async function pollLearningJob(
  jobId: string,
  opts?: { intervalMs?: number; timeoutMs?: number },
): Promise<Record<string, unknown>> {
  const intervalMs = opts?.intervalMs ?? 2000;
  const timeoutMs = opts?.timeoutMs ?? 300_000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const row = await api.get<JobRow>(`/learning/jobs/${jobId}`);
    if (row.status === 'completed') {
      return (row.result as Record<string, unknown>) ?? {};
    }
    if (row.status === 'failed') {
      throw new Error(row.error || '学习任务失败');
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('学习任务仍在后台运行，请稍后在「任务历史」中查看');
}

export function isAsyncLearningJob(
  out: Record<string, unknown>,
): out is { job_id: string; poll: string } {
  return typeof out.job_id === 'string' && out.status === 'accepted';
}
