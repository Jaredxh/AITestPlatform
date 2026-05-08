/**
 * Phase 13 / Task 13.3 — ConfirmationCard / TaskBadge / ExecutionEventCard
 * 三个新组件共享的 TS 类型；与后端 ``schemas.py``：
 *   ``app/modules/skills/builtin/ui_automation/schemas.py:ExecutionPlanCard``
 *   ``app/modules/llm/system_event_service.py:publish_execution_done``
 * 严格对齐。
 *
 * 命名风格沿用后端 snake_case；前端不做镜像转换以减少同步成本——OpenAPI 生
 * 成型工作 task 13.4 + 后会统一接入。
 */

export type ConfirmationStrength = "none" | "soft" | "strict";
export type EnvRiskLevel = "low" | "medium" | "high";

export interface CaseSummary {
  id: string;
  case_no: number;
  title: string;
  priority: string;
  status: string;
  relevance_score?: number | null;
  matched_via: string[];
}

export interface EnvironmentSummary {
  id: string;
  name: string;
  base_url: string;
  risk_level: EnvRiskLevel;
  risk_reason?: string | null;
}

export interface LLMProviderSummary {
  id: string | null;
  name: string;
  provider: string;
  model: string;
}

export interface TestDataPreviewItem {
  semantic: string | null;
  key: string;
  value_preview: string;
  source: string;
  source_set_id: string | null;
  is_secret: boolean;
}

export interface TestDataPreview {
  items: TestDataPreviewItem[];
  missing_semantics: string[];
  set_summaries: Array<{
    id: string;
    name: string;
    scope: string;
    item_count: number;
  }>;
}

export interface ExecutionPlanCard {
  plan_id: string;
  project_id: string;
  cases: CaseSummary[];
  environment: EnvironmentSummary;
  llm_provider: LLMProviderSummary;
  test_data_preview: TestDataPreview;
  estimated_duration_seconds: number;
  confirmation_strength: ConfirmationStrength;
  confirmation_payload: {
    message?: string;
    challenge?: string;
    challenge_value?: string;
    ack_label?: string;
  } & Record<string, unknown>;
  expires_at: string | null;
  skill_card_message_id?: string | null;
}

/** TaskBadge 元数据（落在 chat_messages.meta_data 上）。 */
export interface TaskBadgeMeta {
  task_id: string;
  status?: string;
  /** 后端 ExecutionListItem 的简化镜像（已派发后即时回填）。 */
  total_cases?: number;
  passed_cases?: number;
  failed_cases?: number;
  skipped_cases?: number;
  duration_ms?: number | null;
  /** ConfirmationCard 派发前 plan_id；用户重启浏览器后能反查到 plan 副本。 */
  plan_id?: string;
  /** 派发时记录的环境 / 用例展示信息（变身后供 TaskBadge 渲染顶部摘要）。 */
  title?: string;
  environment_name?: string;
}

/** ExecutionEventCard 元数据（落在 chat_messages.meta_data 上）。 */
export interface ExecutionEventMeta {
  task_id: string;
  result: {
    title?: string;
    status?: string;
    total_cases?: number;
    passed_cases?: number;
    failed_cases?: number;
    skipped_cases?: number;
    duration_ms?: number | null;
    error_message?: string | null;
    [k: string]: unknown;
  };
}
