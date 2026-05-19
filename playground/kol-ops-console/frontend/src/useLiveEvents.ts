import { useEffect, useRef, useState } from 'react';
import { API_BASE, getToken } from './api';

export type WsEvent = {
  type: 'events';
  items: Array<{
    id: number;
    kol_identity_id: number;
    event_type: string;
    stage: string | null;
    sub_status: string | null;
    ts: string;
    actor: string;
    payload_json: string;
  }>;
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
      };
      ws.onmessage = (msg) => {
        try {
          const parsed = JSON.parse(msg.data) as WsEvent;
          cbRef.current(parsed);
        } catch {
          /* ignore non-JSON */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        retry += 1;
        setTimeout(connect, Math.min(30_000, 1_000 * 2 ** retry));
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      stop = true;
      ws?.close();
    };
  }, []);

  return { connected };
}
