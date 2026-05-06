/**
 * SSE 流式处理 composable。
 *
 * 协议：后端用 JSON 编码每个 `data:` 字段（避免 markdown 中的换行被切碎），
 * 例如：
 *   data: {"type":"delta","content":"hello"}
 *   data: {"type":"reasoning","content":"思考..."}
 *   data: {"type":"info","message":"正在联网搜索..."}
 *   data: {"type":"action","content":"...","meta":{...}}
 *   data: {"type":"error","message":"..."}
 *   data: {"type":"done"}
 *
 * 兼容旧格式：若收到非 JSON 的 `data:` 或字符串 `[DONE]`，按文本处理。
 *
 * 注意：SSE 事件之间以 `\n\n` 分隔；同一事件可能跨多个 chunk 到达，
 * 因此必须按 "事件" 而不是按 "行" 缓冲拆分。
 *
 * 设计变更（多并发）：
 *   每次 fetchSSE 都有独立的 AbortController，可以同时存在多个流式请求
 *   （例如用户切换会话后又在新会话发了消息，旧会话的流不会被新流打断）。
 *   返回值带 abort()，调用方可以单独中断某一路。
 */

export interface SSEEvent {
  type: string;
  content?: string;
  message?: string;
  meta?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SSECallbacks {
  onDelta?: (text: string) => void;
  onReasoning?: (text: string) => void;
  onInfo?: (text: string) => void;
  onAction?: (content: string, meta: Record<string, unknown>) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
  /** Catch-all for domain-specific event types (e.g. batch_start / generated). */
  onEvent?: (event: SSEEvent) => void;
}

export interface SSEHandle {
  /** 主动中断本次流式请求，并触发 onDone（视为正常结束）。 */
  abort: () => void;
  /** 等待流式完成，正常结束 / 出错 / 主动中断都会 resolve。 */
  promise: Promise<void>;
}

const activeControllers = new Set<AbortController>();

export function useSSE() {
  function fetchSSE(
    url: string,
    body: Record<string, unknown> | null,
    callbacks: SSECallbacks,
    options: { method?: "GET" | "POST" } = {},
  ): SSEHandle {
    const controller = new AbortController();
    activeControllers.add(controller);

    const promise = (async () => {
      const token = localStorage.getItem("access_token") || "";
      let done = false;
      const ensureDone = () => {
        if (done) return;
        done = true;
        callbacks.onDone?.();
      };

      try {
        const method = options.method || (body ? "POST" : "GET");
        const response = await fetch(url, {
          method,
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: method === "POST" && body ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });

        if (!response.ok) {
          const errText = await response.text().catch(() => "");
          callbacks.onError?.(errText || `HTTP ${response.status}`);
          ensureDone();
          return;
        }

        const reader = response.body?.getReader();
        if (!reader) {
          callbacks.onError?.("无法获取响应流");
          ensureDone();
          return;
        }

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done: readerDone, value } = await reader.read();
          if (readerDone) break;

          buffer += decoder.decode(value, { stream: true });

          let sepIdx: number;
          while ((sepIdx = findEventBoundary(buffer)) !== -1) {
            const eventBlock = buffer.slice(0, sepIdx);
            buffer = buffer.slice(sepIdx).replace(/^\n+/, "");
            const dataPayload = collectDataLines(eventBlock);
            if (dataPayload === null) continue;
            const result = dispatch(dataPayload, callbacks);
            if (result === "done") {
              done = true;
              return;
            }
          }
        }

        if (buffer.trim()) {
          const dataPayload = collectDataLines(buffer);
          if (dataPayload !== null) {
            const result = dispatch(dataPayload, callbacks);
            if (result === "done") {
              done = true;
              return;
            }
          }
        }

        ensureDone();
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // 用户主动中断（点停止 / 删除会话）：触发 onDone 让上层把已生成的内容入库
          ensureDone();
          return;
        }
        callbacks.onError?.(err instanceof Error ? err.message : "流式请求失败");
        ensureDone();
      } finally {
        activeControllers.delete(controller);
      }
    })();

    return {
      abort: () => {
        if (!controller.signal.aborted) controller.abort();
      },
      promise,
    };
  }

  /** 中断所有正在进行的 SSE 请求（页面卸载时使用）。 */
  function abortAll() {
    for (const c of activeControllers) {
      if (!c.signal.aborted) c.abort();
    }
    activeControllers.clear();
  }

  return { fetchSSE, abortAll };
}

function dispatch(raw: string, callbacks: SSECallbacks): "done" | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return;

  if (trimmed === "[DONE]") {
    callbacks.onDone?.();
    return "done";
  }

  let event: SSEEvent | null = null;
  if (trimmed.startsWith("{")) {
    try {
      event = JSON.parse(trimmed) as SSEEvent;
    } catch {
      event = null;
    }
  }

  if (!event) {
    callbacks.onDelta?.(raw);
    return;
  }

  switch (event.type) {
    case "delta":
      if (event.content) callbacks.onDelta?.(event.content);
      callbacks.onEvent?.(event);
      return;
    case "reasoning":
      if (event.content) callbacks.onReasoning?.(event.content);
      callbacks.onEvent?.(event);
      return;
    case "info":
      if (event.message) callbacks.onInfo?.(event.message);
      callbacks.onEvent?.(event);
      return;
    case "action":
      callbacks.onAction?.(event.content || "", event.meta || {});
      callbacks.onEvent?.(event);
      return;
    case "error":
      callbacks.onError?.(event.message || "未知错误");
      callbacks.onEvent?.(event);
      return;
    case "done":
      callbacks.onEvent?.(event);
      callbacks.onDone?.();
      return "done";
    default:
      // Domain-specific events (testcase batch_start / generated, etc.) are
      // delivered to onEvent. Older plain-JSON streams without a type still
      // fall back to onDelta so existing integrations keep working.
      if (callbacks.onEvent) {
        callbacks.onEvent(event);
      } else {
        callbacks.onDelta?.(raw);
      }
  }
}

function findEventBoundary(buffer: string): number {
  const idx1 = buffer.indexOf("\n\n");
  const idx2 = buffer.indexOf("\r\n\r\n");
  if (idx1 === -1) return idx2;
  if (idx2 === -1) return idx1;
  return Math.min(idx1, idx2);
}

function collectDataLines(eventBlock: string): string | null {
  const lines = eventBlock.split(/\r?\n/);
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("data:")) {
      // 规范上 "data: " 后有一个空格，但兼容没有空格的实现
      dataLines.push(line.slice(5).replace(/^ /, ""));
    } else if (line.startsWith("event:")) {
      // 暂不处理具名事件，所有信息都通过 data 的 JSON 区分
      continue;
    } else if (line.startsWith(":")) {
      // SSE 注释行
      continue;
    }
  }
  if (dataLines.length === 0) return null;
  return dataLines.join("\n");
}
