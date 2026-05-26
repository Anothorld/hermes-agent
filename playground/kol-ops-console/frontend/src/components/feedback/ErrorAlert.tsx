import { formatApiError } from '../../lib/errors';

interface Props {
  error: unknown;
  onRetry?: () => void;
  // For inline (page-header) errors. Defaults to a bordered alert box.
  compact?: boolean;
}

// Renders normalised API errors. Use this anywhere a page used to do
// `<div className="text-red-600">{String(ex)}</div>`. If the error is
// classified as retryable and the caller supplies onRetry, a "重试"
// button is rendered.

export function ErrorAlert({ error, onRetry, compact = false }: Props) {
  if (!error) return null;
  const { title, detail, retryable } = formatApiError(error);
  if (compact) {
    return (
      <div className="flex items-center gap-2 text-sm text-rose-700">
        <span className="font-medium">{title}</span>
        {detail && <span className="text-xs text-rose-600">{detail}</span>}
        {retryable && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="ml-1 rounded border border-rose-200 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-100"
          >
            重试
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-rose-100 text-xs font-bold text-rose-700">
          !
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-medium">{title}</div>
          {detail && (
            <div className="mt-0.5 break-words text-xs leading-snug opacity-90">
              {detail}
            </div>
          )}
        </div>
        {retryable && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="ml-1 flex-shrink-0 rounded border border-rose-300 bg-white px-2 py-1 text-xs text-rose-700 hover:bg-rose-100"
          >
            重试
          </button>
        )}
      </div>
    </div>
  );
}
