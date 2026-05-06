/**
 * 提示词分类、子分类、可用变量的中文元数据。
 */

export const PROMPT_CATEGORY_OPTIONS = [
  { label: "对话", value: "chat" },
  { label: "评审", value: "review" },
  { label: "生成", value: "generation" },
  { label: "UI 测试", value: "ui_test" },
  { label: "自定义", value: "custom" },
];

export const PROMPT_CATEGORY_LABEL: Record<string, string> = Object.fromEntries(
  PROMPT_CATEGORY_OPTIONS.map((o) => [o.value, o.label]),
);

/** 子分类的中文映射（按 category 分组） */
export const PROMPT_SUB_CATEGORY_OPTIONS: Record<string, { label: string; value: string }[]> = {
  chat: [
    { label: "通用助手", value: "general" },
    { label: "测试专家", value: "testing_expert" },
    { label: "代码评审官", value: "code_reviewer" },
    { label: "开发专家", value: "dev_expert" },
  ],
  review: [
    { label: "完整性分析", value: "completeness" },
    { label: "可测性分析", value: "testability" },
    { label: "清晰度分析", value: "clarity" },
    { label: "风险识别", value: "risk" },
  ],
  generation: [
    { label: "功能用例", value: "functional" },
    { label: "边界用例", value: "boundary" },
  ],
  ui_test: [
    { label: "UI 自动化", value: "ui_auto" },
  ],
  custom: [],
};

const SUB_CATEGORY_LABEL_FLAT: Record<string, string> = (() => {
  const m: Record<string, string> = {};
  for (const opts of Object.values(PROMPT_SUB_CATEGORY_OPTIONS)) {
    for (const o of opts) m[o.value] = o.label;
  }
  return m;
})();

export function subCategoryLabel(value: string | null | undefined): string {
  if (!value) return "";
  return SUB_CATEGORY_LABEL_FLAT[value] || value;
}

/**
 * 内置可用变量。`source` 含义：
 * - context：来自当前上下文（项目 / 用户）自动注入
 * - auto：被业务流程（如评审、用例生成）自动填充
 * - manual：用户手动输入
 */
export interface PromptVariableMeta {
  name: string;
  label: string;
  description: string;
  source: "context" | "auto" | "manual";
  scope: "chat" | "review" | "generation" | "common";
}

export const BUILT_IN_PROMPT_VARIABLES: PromptVariableMeta[] = [
  {
    name: "project_name",
    label: "项目名称",
    description: "当前所在项目的名称，自动取自项目空间。",
    source: "context",
    scope: "common",
  },
  {
    name: "user_name",
    label: "用户名",
    description: "当前登录用户的显示名。",
    source: "context",
    scope: "common",
  },
  {
    name: "current_date",
    label: "当前日期",
    description: "提示词渲染时的当前日期（YYYY-MM-DD）。",
    source: "context",
    scope: "common",
  },
  {
    name: "doc_content",
    label: "文档内容",
    description: "需求文档全文，由「需求评审 / 用例生成」流程自动注入。",
    source: "auto",
    scope: "review",
  },
  {
    name: "output_format",
    label: "输出格式",
    description: "约束 AI 输出结构（如 Markdown 表格、JSON Schema 等），由调用方自动注入。",
    source: "auto",
    scope: "review",
  },
  {
    name: "module_name",
    label: "模块名称",
    description: "当前选中的功能模块，用于按模块生成用例。",
    source: "auto",
    scope: "generation",
  },
];

export const VARIABLE_META_MAP: Record<string, PromptVariableMeta> = Object.fromEntries(
  BUILT_IN_PROMPT_VARIABLES.map((v) => [v.name, v]),
);

export const VARIABLE_SOURCE_LABEL: Record<PromptVariableMeta["source"], string> = {
  context: "上下文自动",
  auto: "流程自动注入",
  manual: "手动填写",
};
