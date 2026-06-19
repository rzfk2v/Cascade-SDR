// WebSocket client: JSON commands out; JSON status + tagged binary streams in.
// Binary frames carry a 4-byte header (tag + 3 pad bytes) so the payload that
// follows is 4-byte aligned for typed-array views (see backend app/hub.py).
const BINARY_HEADER = 4;

export const FrameTag = {
  FFT: 0x01,
  AUDIO: 0x02,
  APT: 0x03,
  SSTV: 0x04,
} as const;

type JsonHandler = (msg: any) => void;
type BinaryHandler = (tag: number, payload: DataView) => void;

export class SdrSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private jsonHandlers: JsonHandler[] = [];
  private binaryHandlers: BinaryHandler[] = [];
  private reconnectTimer: number | null = null;

  constructor(path = "/ws") {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.url = `${proto}://${location.host}${path}`;
  }

  connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => this.emitJson({ type: "_open" });
    this.ws.onclose = () => {
      this.emitJson({ type: "_close" });
      this.scheduleReconnect();
    };
    this.ws.onerror = () => this.ws?.close();
    this.ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        this.emitJson(JSON.parse(ev.data));
      } else {
        const buf = ev.data as ArrayBuffer;
        const tag = new DataView(buf).getUint8(0);
        const body = new DataView(buf, BINARY_HEADER);
        for (const h of this.binaryHandlers) h(tag, body);
      }
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer != null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 1500);
  }

  private emitJson(msg: any): void {
    for (const h of this.jsonHandlers) h(msg);
  }

  onJson(h: JsonHandler): void {
    this.jsonHandlers.push(h);
  }
  onBinary(h: BinaryHandler): void {
    this.binaryHandlers.push(h);
  }

  send(cmd: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(cmd));
    }
  }
}
