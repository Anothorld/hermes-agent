import { useCallback, useEffect, useRef, useState } from 'react';
import { renderAsync } from 'docx-preview';
import { api, API_BASE, getToken } from '../api';
import { errorSummary } from '../lib/errors';

type ContractPreview = {
  display_name: string;
  filename: string;
  html: string;
  path: string;
};

function isDocxAttachment(path: string): boolean {
  return /\.docx$/i.test(path);
}

function buildContractParams(
  identityId: number,
  campaignId: string,
  env: string,
  attachmentPath?: string,
): URLSearchParams {
  const params = new URLSearchParams({
    identity_id: String(identityId),
    campaign_id: campaignId,
    env,
  });
  if (attachmentPath) {
    params.set('attachment_path', attachmentPath);
  }
  return params;
}

export default function ContractAttachmentPreview({
  identityId,
  campaignId,
  env,
  attachmentPath,
}: {
  identityId: number;
  campaignId: string;
  env: string;
  attachmentPath?: string;
}) {
  const [open, setOpen] = useState(true);
  const [data, setData] = useState<ContractPreview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [renderBusy, setRenderBusy] = useState(false);
  const [usedHtmlFallback, setUsedHtmlFallback] = useState(false);
  const docxBodyRef = useRef<HTMLDivElement>(null);
  const docxStyleRef = useRef<HTMLDivElement>(null);
  const htmlFallbackRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setBusy(true);
    setErr(null);
    setUsedHtmlFallback(false);
    const params = buildContractParams(identityId, campaignId, env, attachmentPath);
    api
      .get<ContractPreview>(`/contracts/preview?${params.toString()}`)
      .then(setData)
      .catch((e) => setErr(errorSummary(e)))
      .finally(() => setBusy(false));
  }, [attachmentPath, campaignId, env, identityId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!open || !data || busy) {
      return undefined;
    }

    let cancelled = false;
    const htmlFallback = data.html;

    async function renderDocxPreview() {
      setRenderBusy(true);
      setErr(null);
      setUsedHtmlFallback(false);

      const params = buildContractParams(identityId, campaignId, env, attachmentPath);
      const token = getToken();

      try {
        const res = await fetch(`${API_BASE}/contracts/download?${params.toString()}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
          throw new Error(`下载失败 (${res.status})`);
        }
        const blob = await res.blob();
        if (cancelled || !docxBodyRef.current) {
          return;
        }

        docxBodyRef.current.innerHTML = '';
        if (docxStyleRef.current) {
          docxStyleRef.current.innerHTML = '';
        }

        await renderAsync(
          blob,
          docxBodyRef.current,
          docxStyleRef.current ?? undefined,
          {
            className: 'docx',
            inWrapper: true,
            ignoreWidth: false,
            ignoreHeight: false,
            ignoreFonts: false,
            breakPages: true,
            ignoreLastRenderedPageBreak: true,
            experimental: true,
            useBase64URL: true,
            renderHeaders: true,
            renderFooters: true,
            renderFootnotes: true,
            renderEndnotes: true,
          },
        );
      } catch {
        if (cancelled) {
          return;
        }
        setUsedHtmlFallback(true);
        if (htmlFallbackRef.current && htmlFallback) {
          htmlFallbackRef.current.innerHTML = htmlFallback;
        }
      } finally {
        if (!cancelled) {
          setRenderBusy(false);
        }
      }
    }

    void renderDocxPreview();

    return () => {
      cancelled = true;
    };
  }, [attachmentPath, busy, campaignId, data, env, identityId, open]);

  const onDownload = async () => {
    const params = buildContractParams(identityId, campaignId, env, attachmentPath);
    const token = getToken();
    const res = await fetch(`${API_BASE}/contracts/download?${params.toString()}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      setErr(`下载失败 (${res.status})`);
      return;
    }
    const blob = await res.blob();
    const disp = res.headers.get('content-disposition') || '';
    const match = /filename="([^"]+)"/.exec(disp);
    const filename = match?.[1] || data?.display_name || data?.filename || 'contract.docx';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (err) {
    return (
      <div className="rounded border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
        合同预览加载失败：{err}
      </div>
    );
  }
  if (busy && !data) {
    return <div className="text-xs text-slate-500">正在加载合同预览…</div>;
  }
  if (!data) {
    return null;
  }

  return (
    <div className="mt-2 rounded border border-indigo-200 bg-indigo-50/40">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-indigo-100 px-3 py-2">
        <div className="min-w-0">
          <div className="text-[11px] font-medium text-indigo-900">合同附件</div>
          <div className="truncate text-xs text-slate-800" title={data.display_name}>
            {data.display_name}
          </div>
          {usedHtmlFallback && (
            <div className="mt-0.5 text-[10px] text-amber-700">
              高保真预览不可用，已降级为简化 HTML 预览
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="rounded border border-indigo-200 bg-white px-2 py-1 text-[11px] text-indigo-900 hover:bg-indigo-50"
          >
            {open ? '收起预览' : '展开预览'}
          </button>
          <button
            type="button"
            onClick={onDownload}
            className="rounded bg-indigo-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-indigo-700"
          >
            下载 Word
          </button>
        </div>
      </div>
      {open && (
        <div className="contract-docx-viewer max-h-[32rem] overflow-auto bg-slate-200 px-3 py-4">
          <div ref={docxStyleRef} className="hidden" aria-hidden="true" />
          {renderBusy && (
            <div className="mb-2 text-center text-xs text-slate-600">正在渲染 Word 版式…</div>
          )}
          <div
            ref={docxBodyRef}
            className="contract-docx-canvas mx-auto min-h-[11in] max-w-[8.5in] bg-white shadow-md"
          />
          <div
            ref={htmlFallbackRef}
            className={[
              'contract-docx-preview px-4 py-3 text-sm leading-relaxed text-slate-800',
              '[&_.contract-table]:my-2 [&_.contract-table]:w-full [&_.contract-table]:border-collapse',
              '[&_.contract-table_td]:border [&_.contract-table_td]:border-slate-300',
              '[&_.contract-table_td]:px-2 [&_.contract-table_td]:py-1 [&_.contract-table_td]:align-top',
              '[&_p]:my-2',
              usedHtmlFallback ? 'block' : 'hidden',
            ].join(' ')}
          />
        </div>
      )}
    </div>
  );
}

export function attachmentDisplayName(path: string): string {
  const base = path.split('/').pop() || path;
  if (base.startsWith('POVISON_Influencer_Agreement_')) {
    const core = base.slice('POVISON_Influencer_Agreement_'.length).replace(/\.docx$/i, '');
    return `POVISON Influencer Agreement — ${core.replace(/_/g, ' ')}.docx`;
  }
  return base;
}

export function hasDocxAttachments(attachments: unknown[]): boolean {
  return attachments.some((a) => typeof a === 'string' && isDocxAttachment(a));
}
