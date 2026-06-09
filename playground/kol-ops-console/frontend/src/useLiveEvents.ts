import { useContext, useEffect, useRef } from 'react';
import { LiveEventsContext } from './LiveEventsProvider';

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

/** Subscribe to the singleton live-events websocket (via LiveEventsProvider). */
export function useLiveEvents(onEvent: (e: WsEvent) => void): { connected: boolean } {
  const ctx = useContext(LiveEventsContext);
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;

  useEffect(() => {
    if (!ctx) return;
    return ctx.subscribe((e) => cbRef.current(e));
  }, [ctx]);

  return { connected: ctx?.connected ?? false };
}
