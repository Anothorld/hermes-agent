import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { API_BASE, getToken } from './api';
import type { WsEvent } from './useLiveEvents';

type Subscriber = (event: WsEvent) => void;

type LiveEventsContextValue = {
  connected: boolean;
  subscribe: (fn: Subscriber) => () => void;
};

export const LiveEventsContext = createContext<LiveEventsContextValue | null>(null);

export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const subsRef = useRef(new Set<Subscriber>());

  const subscribe = useCallback((fn: Subscriber) => {
    subsRef.current.add(fn);
    return () => {
      subsRef.current.delete(fn);
    };
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setConnected(false);
      return;
    }
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
          subsRef.current.forEach((fn) => fn(parsed));
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
      setConnected(false);
    };
  }, []);

  return (
    <LiveEventsContext.Provider value={{ connected, subscribe }}>
      {children}
    </LiveEventsContext.Provider>
  );
}

export function useLiveEventsContext(): LiveEventsContextValue {
  const ctx = useContext(LiveEventsContext);
  if (!ctx) {
    throw new Error('useLiveEventsContext requires LiveEventsProvider');
  }
  return ctx;
}

export function useLiveEventsConnected(): boolean {
  const ctx = useContext(LiveEventsContext);
  return ctx?.connected ?? false;
}
