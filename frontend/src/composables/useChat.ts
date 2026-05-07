import { ref, computed } from "vue";
import {
  getSessionsApi,
  createSessionApi,
  getSessionDetailApi,
  deleteSessionApi,
  startChatTaskApi,
  updateSessionApi,
  uploadFileApi,
} from "@/services/chat";
import type { ChatSession, ChatMessage, FileUploadResult } from "@/services/chat";
import type { SkillActivatedEvent } from "@/services/skills";
import { useSSE } from "./useSSE";

export interface PendingFile {
  id: string;
  file: File;
  status: "uploading" | "done" | "error";
  result?: FileUploadResult;
  error?: string;
}

/** 单个会话当前正在生成中的内容快照。 */
export interface StreamingState {
  /** 触发本次流的会话 id；用于切换会话时按 sessionId 区分。 */
  sessionId: string;
  /** 模型正文增量（markdown 原文）。 */
  content: string;
  /** 模型思考链增量（reasoning_content）。 */
  reasoning: string;
  /** 状态/提示信息（联网中、生成中…），按顺序追加。 */
  infos: string[];
}

const EMPTY_STREAM: StreamingState = {
  sessionId: "",
  content: "",
  reasoning: "",
  infos: [],
};

function emptyStream(sessionId = ""): StreamingState {
  return { sessionId, content: "", reasoning: "", infos: [] };
}

export function useChat() {
  const sessions = ref<ChatSession[]>([]);
  const currentSessionId = ref<string | null>(null);
  /**
   * 每个会话独立保存自己的消息列表，切回去能立刻复用、不需要重新 fetch；
   * 也避免会话 A 还在流式生成时，切到会话 B 再切回 A 看到"答案丢失"。
   */
  const messagesBySession = ref<Record<string, ChatMessage[]>>({});
  /**
   * Phase 12 / Task 12.6 — 最近一条 skill_activated SSE 事件。
   * 仅当当前会话产生时才会被赋值；ChatView 把它 watch 给 SkillActivationHint。
   */
  const latestSkillActivation = ref<SkillActivatedEvent | null>(null);
  /** 各会话独立的流式中间态：切到 B 再切回 A 时还能看到 A 正在生成的内容。 */
  const streamsBySession = ref<Record<string, StreamingState>>({});
  /** 各会话独立的"是否在流式中"，用于停止按钮和"是否要在会话列表上显示动画"。 */
  const streamingSessions = ref<Record<string, boolean>>({});
  const isLoadingSessions = ref(false);
  const isLoadingMessages = ref(false);
  const pendingFiles = ref<PendingFile[]>([]);

  const { fetchSSE } = useSSE();

  /** 每个会话当前的中断句柄；切到别的会话不再 abort，让上一路自然跑完。 */
  const abortHandles: Record<string, () => void> = {};

  const currentSession = computed(() =>
    sessions.value.find((s) => s.id === currentSessionId.value) ?? null,
  );

  const messages = computed<ChatMessage[]>(() =>
    currentSessionId.value
      ? messagesBySession.value[currentSessionId.value] || []
      : [],
  );

  /** 当前会话当前的流式快照（无流时是空对象，渲染层据此判断是否显示"正在生成"气泡）。 */
  const streaming = computed<StreamingState>(() => {
    const sid = currentSessionId.value;
    if (!sid) return { ...EMPTY_STREAM };
    return streamsBySession.value[sid] || { ...EMPTY_STREAM };
  });

  /** 当前会话是否处于流式中（用于禁用输入框/显示停止按钮）。 */
  const isStreaming = computed<boolean>(() => {
    const sid = currentSessionId.value;
    if (!sid) return false;
    return !!streamingSessions.value[sid];
  });

  /** 兼容老调用：当前可见的正文增量。 */
  const streamingContent = computed(() => streaming.value.content);

  function _setStream(sessionId: string, patch: Partial<StreamingState>) {
    const cur =
      streamsBySession.value[sessionId] || emptyStream(sessionId);
    streamsBySession.value = {
      ...streamsBySession.value,
      [sessionId]: { ...cur, sessionId, ...patch },
    };
  }

  function _clearStream(sessionId: string) {
    if (sessionId in streamsBySession.value) {
      const next = { ...streamsBySession.value };
      delete next[sessionId];
      streamsBySession.value = next;
    }
  }

  function _setStreamingFlag(sessionId: string, value: boolean) {
    const next = { ...streamingSessions.value };
    if (value) next[sessionId] = true;
    else delete next[sessionId];
    streamingSessions.value = next;
  }

  function _appendMessage(sessionId: string, msg: ChatMessage) {
    const cur = messagesBySession.value[sessionId] || [];
    messagesBySession.value = {
      ...messagesBySession.value,
      [sessionId]: [...cur, msg],
    };
  }

  function _setMessages(sessionId: string, list: ChatMessage[]) {
    messagesBySession.value = {
      ...messagesBySession.value,
      [sessionId]: list,
    };
  }

  async function loadSessions(projectId?: string) {
    isLoadingSessions.value = true;
    try {
      const res = await getSessionsApi(projectId);
      if (res.success) {
        sessions.value = res.data;
        const currentStillExists =
          currentSessionId.value &&
          sessions.value.some((s) => s.id === currentSessionId.value);
        if (!currentStillExists) {
          const latest = sessions.value[0];
          if (latest) {
            await selectSession(latest.id);
          } else {
            currentSessionId.value = null;
          }
        } else if (currentSessionId.value) {
          // 路由切出去再切回来 / 组件 remount 时，currentSessionId 可能还在
          // （pinia / composable 保留），但消息列表也早就在本地缓存里。
          // 这种情况 selectSession 不会再跑，需要我们手动检查一下：
          // 如果最后一条 assistant 还在 streaming，就把它续上。
          if (!messagesBySession.value[currentSessionId.value]?.length) {
            // 消息没缓存（例如 pinia 保留了 id 但首次拉 list）——拉一次详情。
            await selectSession(currentSessionId.value);
          } else {
            resumeIfStreaming(currentSessionId.value);
          }
        }
      }
    } finally {
      isLoadingSessions.value = false;
    }
  }

  async function selectSession(sessionId: string) {
    if (currentSessionId.value === sessionId) return;
    // 关键：不要中断流也不要清掉旧会话的 streaming/messages，
    // 这样切回去还能看到上次正在写的内容。
    currentSessionId.value = sessionId;

    // 已经有缓存就不重新拉了，避免覆盖正在 push 的本地消息
    if (messagesBySession.value[sessionId]?.length) {
      // 已有本地缓存时，仍然尝试 resume（比如刷新后第一次从 SPA 内切回来）。
      resumeIfStreaming(sessionId);
      return;
    }

    isLoadingMessages.value = true;
    try {
      const res = await getSessionDetailApi(sessionId);
      if (res.success) {
        _setMessages(sessionId, res.data.messages);
        // 刷新 / 首次���载后：若最后一条 assistant 还是 streaming 状态，
        // 直接把后台任务续订回前端。
        resumeIfStreaming(sessionId);
      }
    } finally {
      isLoadingMessages.value = false;
    }
  }

  async function createNewSession(llmConfigId?: string, projectId?: string, systemPrompt?: string) {
    const res = await createSessionApi({
      llm_config_id: llmConfigId,
      project_id: projectId,
      system_prompt: systemPrompt,
    });
    if (res.success) {
      sessions.value.unshift(res.data);
      _setMessages(res.data.id, []);
      await selectSession(res.data.id);
      return res.data;
    }
    return null;
  }

  async function deleteSession(sessionId: string) {
    const res = await deleteSessionApi(sessionId);
    if (res.success) {
      sessions.value = sessions.value.filter((s) => s.id !== sessionId);
      if (streamingSessions.value[sessionId]) {
        abortHandles[sessionId]?.();
      }
      delete abortHandles[sessionId];
      _setStreamingFlag(sessionId, false);
      _clearStream(sessionId);
      const next = { ...messagesBySession.value };
      delete next[sessionId];
      messagesBySession.value = next;
      if (currentSessionId.value === sessionId) {
        currentSessionId.value = null;
      }
    }
  }

  async function renameSession(sessionId: string, title: string) {
    const res = await updateSessionApi(sessionId, { title });
    if (res.success) {
      const idx = sessions.value.findIndex((s) => s.id === sessionId);
      if (idx >= 0) sessions.value[idx] = { ...sessions.value[idx], ...res.data };
    }
  }

  /** 将一段已渲染的 system prompt 同步到当前会话；切换 prompt 后应立即生效。 */
  async function applySystemPrompt(systemPrompt: string | null) {
    if (!currentSessionId.value) return;
    const res = await updateSessionApi(currentSessionId.value, {
      system_prompt: systemPrompt ?? "",
    });
    if (res.success) {
      const idx = sessions.value.findIndex((s) => s.id === currentSessionId.value);
      if (idx >= 0) sessions.value[idx] = { ...sessions.value[idx], ...res.data };
    }
  }

  async function addFile(file: File) {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const pending: PendingFile = { id, file, status: "uploading" };
    pendingFiles.value.push(pending);

    try {
      const res = await uploadFileApi(file);
      if (res.success) {
        pending.status = "done";
        pending.result = res.data;
      } else {
        pending.status = "error";
        pending.error = "上传失败";
      }
    } catch (err: unknown) {
      pending.status = "error";
      pending.error =
        err instanceof Error ? err.message : "上传失败";
    }
  }

  function removeFile(id: string) {
    pendingFiles.value = pendingFiles.value.filter((f) => f.id !== id);
  }

  function clearFiles() {
    pendingFiles.value = [];
  }

  function _buildContent(text: string): string {
    const doneFiles = pendingFiles.value.filter(
      (f) => f.status === "done" && f.result,
    );
    if (doneFiles.length === 0) return text;

    const parts: string[] = [];
    for (const f of doneFiles) {
      if (f.result!.type === "document" && f.result!.text) {
        parts.push(
          `[附件: ${f.result!.filename}]\n\`\`\`\n${f.result!.text.slice(0, 15000)}\n\`\`\``,
        );
      } else if (f.result!.type === "image") {
        parts.push(`[图片: ${f.result!.filename}]`);
      }
    }

    if (parts.length > 0) {
      return `${parts.join("\n\n")}\n\n${text}`;
    }
    return text;
  }

  /**
   * 订阅某条 assistant 消息的生成流；断开 / 切页 / 刷新后重新调用同样的 id
   * 就能接着拿到后续事件 + 最终内容。
   *
   * 关键设计：
   *   - 订阅过程不再"创建消息"，它只是"看已经在后台跑的那条"。
   *   - abort() 仅断开当前 HTTP 连接，不影响后台任务。
   *   - 订阅结束（done / error / abort）后，才把累积的 content 落成正式
   *     assistant 消息，合并进 messagesBySession。
   */
  function _subscribeAssistantMessage(opts: {
    sessionId: string;
    assistantMsgId: string;
    /** 用于 streaming 期间在会话列表上显示更新时间 / 预览标题。 */
    userText: string;
    /** 订阅前已经累积的 content（从已保存的占位消息里拿到的）。 */
    initialContent?: string;
    initialReasoning?: string;
  }) {
    const { sessionId, assistantMsgId, userText, initialContent, initialReasoning } = opts;

    _setStreamingFlag(sessionId, true);
    _setStream(sessionId, {
      content: initialContent || "",
      reasoning: initialReasoning || "",
      infos: [],
    });

    let finished = false;
    const finalize = (errorMsg?: string) => {
      if (finished) return;
      finished = true;

      const stream =
        streamsBySession.value[sessionId] || emptyStream(sessionId);

      if (stream.content) {
        const trailingError = errorMsg ? `\n\n> ⚠️ ${errorMsg}` : "";
        _appendMessage(sessionId, {
          id: assistantMsgId,
          session_id: sessionId,
          role: "assistant",
          content: stream.content + trailingError,
          tokens_used: null,
          model_used: null,
          meta_data: stream.reasoning ? { reasoning: stream.reasoning } : null,
          created_at: new Date().toISOString(),
        });
      } else if (errorMsg) {
        _appendMessage(sessionId, {
          id: assistantMsgId,
          session_id: sessionId,
          role: "assistant",
          content: `> ⚠️ ${errorMsg}`,
          tokens_used: null,
          model_used: null,
          meta_data: null,
          created_at: new Date().toISOString(),
        });
      }

      const idx = sessions.value.findIndex((s) => s.id === sessionId);
      if (idx >= 0) {
        const session = sessions.value[idx];
        sessions.value[idx] = {
          ...session,
          updated_at: new Date().toISOString(),
          title:
            session.title === "新对话" && userText
              ? userText.slice(0, 50) || "新对话"
              : session.title,
        };
        const [updated] = sessions.value.splice(idx, 1);
        sessions.value.unshift(updated);
      }

      _setStreamingFlag(sessionId, false);
      _clearStream(sessionId);
    };

    const handle = fetchSSE(
      `/api/chat/messages/${assistantMsgId}/stream`,
      null,
      {
        onDelta(delta) {
          const cur = streamsBySession.value[sessionId] || emptyStream(sessionId);
          _setStream(sessionId, { content: cur.content + delta });
        },
        onReasoning(reason) {
          const cur = streamsBySession.value[sessionId] || emptyStream(sessionId);
          _setStream(sessionId, { reasoning: cur.reasoning + reason });
        },
        onInfo(message) {
          const cur = streamsBySession.value[sessionId] || emptyStream(sessionId);
          _setStream(sessionId, { infos: [...cur.infos, message] });
        },
        onAction(actionContent) {
          // action 事件 content 是该轮意图的最终内容，覆盖（而不是追加）。
          _setStream(sessionId, { content: actionContent });
        },
        onEvent(event) {
          // Task 12.6 — 后端推 ``skill_activated`` 事件时，仅当对应是当前
          // 可见会话时再亮出 banner，避免后台另一会话的激活提示干扰用户。
          if (
            event.type === "skill_activated" &&
            sessionId === currentSessionId.value
          ) {
            const skillId = String(event.skill_id ?? "");
            const slug = String(event.slug ?? "");
            const name = String(event.name ?? "");
            const reason = String(event.activation_reason ?? "manual") as
              SkillActivatedEvent["activation_reason"];
            if (skillId && slug && name) {
              latestSkillActivation.value = {
                skill_id: skillId,
                slug,
                name,
                activation_reason: reason,
                matched_trigger:
                  typeof event.matched_trigger === "string"
                    ? event.matched_trigger
                    : null,
              };
            }
          }
        },
        onError(msg) {
          finalize(msg);
        },
        onDone() {
          finalize();
        },
      },
      { method: "GET" },
    );
    abortHandles[sessionId] = handle.abort;
    void handle.promise.finally(() => {
      if (abortHandles[sessionId] === handle.abort) {
        delete abortHandles[sessionId];
      }
    });
    return handle;
  }

  async function sendMessage(text: string, llmConfigId?: string) {
    if (!currentSessionId.value) return;
    const sessionId = currentSessionId.value;
    if (streamingSessions.value[sessionId]) return;

    const content = _buildContent(text);
    clearFiles();

    // 乐观更新用户消息（正式 id 由 start API 返回，这里先占位）。
    const optimisticUserId = `temp-${Date.now()}`;
    const userMsg: ChatMessage = {
      id: optimisticUserId,
      session_id: sessionId,
      role: "user",
      content,
      tokens_used: null,
      model_used: null,
      meta_data: null,
      created_at: new Date().toISOString(),
    };
    _appendMessage(sessionId, userMsg);

    let startResp;
    try {
      startResp = await startChatTaskApi(sessionId, {
        content,
        llm_config_id: llmConfigId,
      });
    } catch (err: unknown) {
      _appendMessage(sessionId, {
        id: `err-${Date.now()}`,
        session_id: sessionId,
        role: "assistant",
        content: `> ⚠️ ${err instanceof Error ? err.message : "发起对话失败"}`,
        tokens_used: null,
        model_used: null,
        meta_data: null,
        created_at: new Date().toISOString(),
      });
      return;
    }

    if (!startResp?.success || !startResp.data?.assistant_message_id) {
      _appendMessage(sessionId, {
        id: `err-${Date.now()}`,
        session_id: sessionId,
        role: "assistant",
        content: `> ⚠️ ${startResp?.message || "发起对话失败"}`,
        tokens_used: null,
        model_used: null,
        meta_data: null,
        created_at: new Date().toISOString(),
      });
      return;
    }

    // 用后端返回的正式 id 替换占位 user 消息（保证后续加载不会重复）。
    const list = messagesBySession.value[sessionId] || [];
    const patched = list.map((m) =>
      m.id === optimisticUserId
        ? { ...m, id: startResp!.data.user_message_id }
        : m,
    );
    _setMessages(sessionId, patched);

    // 更新会话卡片 message_count：用户 + 占位 assistant = +2。
    const sidx = sessions.value.findIndex((s) => s.id === sessionId);
    if (sidx >= 0) {
      sessions.value[sidx] = {
        ...sessions.value[sidx],
        message_count: (sessions.value[sidx].message_count || 0) + 2,
      };
    }

    _subscribeAssistantMessage({
      sessionId,
      assistantMsgId: startResp.data.assistant_message_id,
      userText: text,
    });
  }

  /**
   * 加载会话消息后调用：若最后一条 assistant 消息的 meta_data.status 是
   * "streaming"，说明上一次发送时后台任务仍在跑，直接把它 resubscribe 回来，
   * 让用户刷新 / 切回来也能接着看到流式输出。
   */
  function resumeIfStreaming(sessionId: string) {
    const list = messagesBySession.value[sessionId] || [];
    if (list.length === 0) return;
    const last = list[list.length - 1];
    if (last.role !== "assistant") return;
    const meta = (last.meta_data || {}) as Record<string, unknown>;
    if (meta.status !== "streaming") return;
    if (streamingSessions.value[sessionId]) return;

    // 找到上一条 user 消息，用它的 content 作为标题兜底。
    const prevUser = [...list].reverse().find((m) => m.role === "user");
    const userText = prevUser?.content || "";

    // resubscribe 前把占位 assistant 消息从列表里摘掉 —— finalize 会用相同的
    // id 把它重新 append 回来，避免 UI 上看到"两条 assistant"。
    _setMessages(
      sessionId,
      list.filter((m) => m.id !== last.id),
    );

    _subscribeAssistantMessage({
      sessionId,
      assistantMsgId: last.id,
      userText,
      initialContent: typeof last.content === "string" ? last.content : "",
      initialReasoning:
        typeof meta.reasoning === "string" ? (meta.reasoning as string) : "",
    });
  }

  function stopGeneration() {
    const sid = currentSessionId.value;
    if (!sid) return;
    // 只 abort 当前这条 HTTP 订阅；后台任务自己会继续跑完并落盘，
    // 下次刷新/切回这条会话时 resumeIfStreaming 会把它续上。
    abortHandles[sid]?.();
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    messages,
    streaming,
    streamingContent,
    isStreaming,
    streamingSessions,
    isLoadingSessions,
    isLoadingMessages,
    pendingFiles,
    latestSkillActivation,
    loadSessions,
    selectSession,
    createNewSession,
    deleteSession,
    renameSession,
    applySystemPrompt,
    sendMessage,
    resumeIfStreaming,
    stopGeneration,
    addFile,
    removeFile,
    clearFiles,
  };
}
