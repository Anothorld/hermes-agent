import { ApiError } from '../api';

// Normalised, operator-facing description of any thrown value coming
// out of the api wrappers. Pages used to dump `String(ex)` into a red
// div, which surfaced things like "TypeError: Failed to fetch" or a
// raw 500-byte JSON body. `formatApiError` classifies the exception
// and returns Chinese sentences plus a `retryable` flag that the UI
// can use to decide whether to render a "重试" button.

export type NormalizedError = {
  title: string;
  detail: string;
  retryable: boolean;
};

export function formatApiError(ex: unknown): NormalizedError {
  if (ex instanceof ApiError) {
    switch (ex.status) {
      case 400:
        return {
          title: '请求格式有误',
          detail: extractDetail(ex.body) ?? '服务器拒绝了请求',
          retryable: false,
        };
      case 401:
        return { title: '会话已过期', detail: '请重新登录后再试', retryable: false };
      case 403:
        return { title: '没有权限', detail: '当前账号不允许执行该操作', retryable: false };
      case 404:
        return {
          title: '资源不存在',
          detail: extractDetail(ex.body) ?? '请求的对象未找到',
          retryable: false,
        };
      case 409:
        return {
          title: '操作冲突',
          detail: extractDetail(ex.body) ?? '状态已经变化，请刷新后重试',
          retryable: true,
        };
      case 422:
        return {
          title: '内容校验失败',
          detail: extractDetail(ex.body) ?? '服务器拒绝了该请求',
          retryable: false,
        };
      case 429:
        return { title: '请求过于频繁', detail: '稍候片刻再试', retryable: true };
      default:
        if (ex.status >= 500) {
          return {
            title: '服务器暂时不可用',
            detail: extractDetail(ex.body) ?? '稍后可点击重试',
            retryable: true,
          };
        }
        return {
          title: `请求失败 (${ex.status})`,
          detail: extractDetail(ex.body) ?? truncate(ex.body, 200),
          retryable: true,
        };
    }
  }
  if (ex instanceof Error) {
    if (ex.name === 'AbortError') {
      return { title: '请求被中断', detail: '', retryable: true };
    }
    if (
      ex.message?.includes('Failed to fetch')
      || ex.message?.includes('NetworkError')
      || ex.message?.includes('Load failed')
    ) {
      return {
        title: '网络不通',
        detail: '稍后会自动重试，也可手动刷新',
        retryable: true,
      };
    }
    return { title: '发生未知错误', detail: ex.message, retryable: true };
  }
  return { title: '发生未知错误', detail: truncate(String(ex), 200), retryable: true };
}

function extractDetail(body: string): string | null {
  if (!body) return null;
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      if (typeof obj.detail === 'string') return obj.detail;
      if (obj.detail && typeof obj.detail === 'object' && !Array.isArray(obj.detail)) {
        const inner = obj.detail as Record<string, unknown>;
        if (inner.code === 'nox_quota_disabled') {
          return (
            '请在本产品 campaign 中展开「编辑 campaign_config」，勾选 nox_quota_enabled 并保存，' +
            '然后再点 Nox 批量尽调。'
          );
        }
        if (inner.code === 'nox_supplement_disabled') {
          return '请先在 campaign_config 中开启 nox_supplement_enabled 并保存，再执行 Nox 补充搜索。';
        }
        if (inner.code === 'nox_quota_exhausted') {
          return (
            'Nox 本月本地 API 预算已用尽。请打开 nox_quota_exhausted 升级或等待下月，' +
            '再继续 LIVE 尽调 / 查邮箱 / 补搜。'
          );
        }
        if (typeof inner.detail === 'string') return inner.detail;
      }
      if (typeof obj.message === 'string') return obj.message;
      if (Array.isArray(obj.detail)) {
        return (obj.detail as unknown[])
          .map((d) => {
            if (d && typeof d === 'object' && 'msg' in (d as object)) {
              return String((d as { msg: unknown }).msg);
            }
            return JSON.stringify(d);
          })
          .join('；');
      }
    }
    return null;
  } catch {
    return body.length < 200 ? body : null;
  }
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

// Quick stringifier when you just need a one-line summary (e.g. toast).
export function errorSummary(ex: unknown): string {
  const { title, detail } = formatApiError(ex);
  return detail ? `${title}：${detail}` : title;
}
