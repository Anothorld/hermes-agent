import { useEffect, useRef, useState } from 'react';
import { API_BASE, getToken } from './api';

export type BridgeEventItem = {
  id: number;
  kol_identity_id: number;
  event_type: string;
  stage: string | null;
  sub_status: string | null;
  ts: string;
  actor: string;
  payload_json: string;
};

export type GatewayApprovalRequestItem = {
  event: 'gateway_approval.request';
  run_id: string;
  campaign_id: string | null;
  kind: 'outreach' | 'reply' | 'draft' | 'resume' | 'refine' | string;
  command: string;
  description: string;
  pattern_key: string;
  pattern_keys?: string[];
  choices: Array<'once' | 'session' | 'always' | 'deny'>;
  source: 'gateway';
  captured_at: string | number;
  seq: number;
};

export type GatewayApprovalResolvedItem = {
  event: 'gateway_approval.responded' | 'gateway_approval.cleared';
  run_id: string;
  reason?: string;
  choice?: 'once' | 'session' | 'always' | 'deny';
  seq: number;
};

export type GatewayApprovalItem =
  | GatewayApprovalRequestItem
  | GatewayApprovalResolvedItem;

export type WsEvent =
  | {
      type: 'events';
      items: BridgeEventItem[];
    }
  | {
      type: 'gateway_approvals';
      items: GatewayApprovalItem[];
    };

export function useLiveEvents(onEvent: (e: WsEvent) => void): { connected: boolean } {
  const [connected, setConnected] = useState(false);
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const url = API_BASE.replace(/^http/, 'ws') + `/ws?token=${encodeURIComponent(token)}`;
    let ws: WebSocket | null = null;
    let retry = 0;
    let stop = false;

    const connect = () => {
      if (stop) return;
      ws = new WebSocket(url);
      ws.onopen = () => {
        setConnected(true);
        retry = 0;
        // #region agent log
        fetch('http://127.0.0.1:7411/ingest/32e61462-f4f7-4538-9c62-3cdb124b8dba',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'bba44f'},body:JSON.stringify({sessionId:'bba44f',location:'useLiveEvents.ts:onopen',message:'ws opened',data:{retry},timestamp:Date.now(),hypothesisId:'B'})}).catch(()=>{});
        // #endregion
      };
      ws.onmessage = (msg) => {
        try {
          const parsed = JSON.parse(msg.data) as WsEvent;
          cbRef.current(parsed);
        } catch {
          /* ignore non-JSON */
        }
      };
      ws.onclose = (ev) => {
        setConnected(false);
        // #region agent log
        fetch('http://127.0.0.1:7411/ingest/32e61462-f4f7-4538-9c62-3cdb124b8dba',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'bba44f'},body:JSON.stringify({sessionId:'bba44f',location:'useLiveEvents.ts:onclose',message:'ws closed',data:{code:ev.code,reason:ev.reason,wasClean:ev.wasClean,stop,retry},timestamp:Date.now(),hypothesisId:'C'})}).catch(()=>{});
        // #endregion
        retry += 1;
        setTimeout(connect, Math.min(30_000, 1_000 * 2 ** retry));
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      stop = true;
      // #region agent log
      fetch('http://127.0.0.1:7411/ingest/32e61462-f4f7-4538-9c62-3cdb124b8dba',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'bba44f'},body:JSON.stringify({sessionId:'bba44f',location:'useLiveEvents.ts:cleanup',message:'effect cleanup closing ws',data:{hadWs:!!ws},timestamp:Date.now(),hypothesisId:'D'})}).catch(()=>{});
      // #endregion
      ws?.close();
    };
  }, []);

  return { connected };
}
