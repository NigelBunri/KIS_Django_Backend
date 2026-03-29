"use client";

import { useEffect, useRef } from "react";

export type SocketEvents = {
  dashboard: {
    activeUsers: number;
    rpm: number;
  };
};

export function useWebsocket(onMessage: (payload: any) => void) {
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const url = (process.env.NEXT_PUBLIC_ADMIN_WS_BASE || "ws://localhost:8000/control/admin/ws") + "/live";
    const socket = new WebSocket(url);
    socketRef.current = socket;

    socket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data);
        onMessage(payload);
      } catch (error) {
        console.error("Invalid websocket payload", error);
      }
    });

    return () => {
      socket.close();
    };
  }, [onMessage]);

  return socketRef.current;
}
