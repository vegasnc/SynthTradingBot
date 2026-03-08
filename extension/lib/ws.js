/** WebSocket stream - reconnects and forwards messages */

import { wsUrl } from "./api.js";

let ws = null;
let unsub = null;

export function stream(onMessage) {
  let active = true;
  function connect() {
    if (!active) return;
    wsUrl().then((url) => {
      const s = new WebSocket(url);
      s.onopen = () => s.send("subscribe");
      s.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          onMessage(msg);
        } catch (_) {}
      };
      s.onclose = () => {
        if (active) setTimeout(connect, 3000);
      };
      s.onerror = () => s.close();
      ws = s;
    });
  }
  connect();
  return () => {
    active = false;
    if (ws) {
      ws.close();
      ws = null;
    }
  };
}
