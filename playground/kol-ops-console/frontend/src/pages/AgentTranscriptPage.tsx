import { Link, useParams, useSearchParams } from 'react-router-dom';
import AgentTranscriptPanel from '../components/AgentTranscriptPanel';
import { useEnvStore } from '../lib/store';

// Full-screen TUI-style view of one campaign's agent transcript. Useful
// when an operator wants to "watch the agent work" without the
// surrounding product / kol panels competing for vertical space.
export function AgentTranscriptPage() {
  const { cid = '' } = useParams();
  const [params] = useSearchParams();
  const storeEnv = useEnvStore((s) => s.env);
  // URL ?env= wins for deep-links (lets transcripts be shared by URL);
  // otherwise use the global env.
  const urlEnv = params.get('env');
  const env: 'TEST' | 'LIVE' =
    urlEnv === 'LIVE' ? 'LIVE' : urlEnv === 'TEST' ? 'TEST' : storeEnv;
  const live = params.get('live') !== '0';
  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">Agent 对话</h1>
          <p className="text-xs text-slate-500">
            campaign <span className="font-mono">{cid}</span> · env{' '}
            <span className="font-mono">{env}</span>
          </p>
        </div>
        <Link to="/products" className="text-sm text-sky-700 hover:underline">
          ← 返回产品列表
        </Link>
      </div>
      <AgentTranscriptPanel campaignId={cid} env={env} live={live} variant="fullscreen" />
    </div>
  );
}
