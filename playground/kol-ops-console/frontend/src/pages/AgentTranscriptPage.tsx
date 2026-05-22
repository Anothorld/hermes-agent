import { Link, useParams, useSearchParams } from 'react-router-dom';
import AgentTranscriptPanel from '../components/AgentTranscriptPanel';

// Full-screen TUI-style view of one campaign's agent transcript. Useful
// when an operator wants to "watch the agent work" without the
// surrounding product / kol panels competing for vertical space.
export function AgentTranscriptPage() {
  const { cid = '' } = useParams();
  const [params] = useSearchParams();
  const env = (params.get('env') === 'LIVE' ? 'LIVE' : 'TEST');
  // `live=1` is the explicit signal from the inline panel that there's
  // an active run worth subscribing to. Default to true so direct links
  // still attempt to stream — the SSE endpoint cleanly emits `closed`
  // when no run is present.
  const live = params.get('live') !== '0';
  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">Agent transcript</h1>
          <p className="text-xs text-slate-500">
            campaign <span className="font-mono">{cid}</span> · env{' '}
            <span className="font-mono">{env}</span>
          </p>
        </div>
        <Link to="/products" className="text-sm text-sky-700 hover:underline">
          ← back to Products
        </Link>
      </div>
      <AgentTranscriptPanel campaignId={cid} env={env} live={live} variant="fullscreen" />
    </div>
  );
}
