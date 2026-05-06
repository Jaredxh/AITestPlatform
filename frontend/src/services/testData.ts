/**
 * 测试物料模块 API 客户端（与后端 app/modules/test_data/schemas.py 严格对齐）。
 *
 * 设计约定：
 * - list / detail 接口中 secret 的 `value_text` 始终为 null，前端通过
 *   `has_secret_value` 判断"已保存 / 未配置"；要看明文必须走 reveal 接口。
 * - file 类型 value_type 的物料下载走 `/file` 路径，二进制响应（非 JSON）。
 * - 随机模板 preview 在前端本地实现，无需后端（random_generator 的模板语法
 *   已经稳定；前端做一份等价的 preview 就可以给用户即时反馈）。
 */

import { request } from "./request";
import type { ApiResponse } from "./auth";

// ─── 基础类型（与后端 schemas 一一对应）─────────────────────────────

export type ValueType =
  | "string"
  | "secret"
  | "multiline"
  | "file"
  | "random"
  | "dataset";

export type DataSetScope = "project" | "environment" | "personal";

export const VALUE_TYPES: ValueType[] = [
  "string",
  "secret",
  "multiline",
  "file",
  "random",
  "dataset",
];

export const VALUE_TYPE_META: Record<
  ValueType,
  { label: string; description: string; icon: string; color: string }
> = {
  string: {
    label: "单行文本",
    description: "普通字符串；可被用例模板 {{key}} 直接引用",
    icon: "i-carbon-string-text",
    color: "default",
  },
  secret: {
    label: "敏感凭据",
    description: "密码 / Token 等；入库加密，不进 LLM 上下文，用 platform_get_secret 获取",
    icon: "i-carbon-password",
    color: "warning",
  },
  multiline: {
    label: "多行文本",
    description: "留言 / JSON payload / 富文本内容等",
    icon: "i-carbon-align-horizontal-left",
    color: "info",
  },
  file: {
    label: "文件物料",
    description: "图片 / 文档 / 附件；AI 通过 platform_get_file 获取本地路径",
    icon: "i-carbon-document",
    color: "success",
  },
  random: {
    label: "随机生成",
    description: "每次执行新实例化（phone:CN / email / uuid / digits:N 等）",
    icon: "i-carbon-reset",
    color: "info",
  },
  dataset: {
    label: "参数化数据组",
    description: "JSON 数组 / 对象；用 platform_iter_dataset 循环使用",
    icon: "i-carbon-data-table",
    color: "default",
  },
};

/** 用户在 category 字段里常会填一些英文 tag（account / login / search / smoke
 *  等）；为了页面观感统一中文，在 **展示** 时做一层最佳努力的英文→中文映
 *  射。**存储**仍然保留用户原值，避免"展示是中文，编辑回显又变英文"这种割
 *  裂感。命中映射时返回中文，未命中时原样返回。 */
const CATEGORY_DISPLAY_MAP: Record<string, string> = {
  account: "账号",
  accounts: "账号",
  login: "登录",
  user: "用户",
  users: "用户",
  password: "密码",
  passwords: "密码",
  secret: "密钥",
  secrets: "密钥",
  token: "令牌",
  tokens: "令牌",
  search: "搜索",
  smoke: "冒烟",
  regression: "回归",
  payment: "支付",
  pay: "支付",
  shipping: "物流",
  delivery: "配送",
  product: "商品",
  products: "商品",
  order: "订单",
  orders: "订单",
  cart: "购物车",
  general: "通用",
  common: "通用",
  default: "默认",
  test: "测试",
  demo: "演示",
};

export function displayCategoryLabel(raw: string | null | undefined): string {
  if (!raw) return "";
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const lower = trimmed.toLowerCase();
  return CATEGORY_DISPLAY_MAP[lower] ?? trimmed;
}

export const SCOPE_META: Record<
  DataSetScope,
  { label: string; icon: string; description: string; color: string }
> = {
  project: {
    label: "项目共享",
    icon: "i-carbon-folder",
    description: "同项目所有成员可见；is_default 为 true 时执行自动加载",
    color: "info",
  },
  environment: {
    label: "环境专属",
    icon: "i-carbon-cloud-services",
    description: "绑定到某个测试环境；只有在跑该环境时注入",
    color: "success",
  },
  personal: {
    label: "个人私有",
    icon: "i-carbon-user-avatar",
    description: "仅你自己可见；常用于存「我专属的账号」",
    color: "warning",
  },
};

// 随机模板支持列表（与后端 random_generator.py 的 _HANDLERS 同步）
export const RANDOM_TEMPLATES: {
  key: string;
  template: string;
  label: string;
  example: string;
}[] = [
  { key: "phone_cn", template: "phone:CN", label: "中国大陆手机号", example: "13812345678" },
  { key: "phone_us", template: "phone:US", label: "美国手机号", example: "18005551234" },
  { key: "email", template: "email", label: "邮箱（example.com）", example: "user123@example.com" },
  { key: "email_gmail", template: "email:gmail.com", label: "邮箱（gmail.com）", example: "alice456@gmail.com" },
  { key: "uuid", template: "uuid", label: "UUID", example: "b6a5c..." },
  { key: "digits8", template: "digits:8", label: "8 位数字", example: "12345678" },
  { key: "digits16", template: "digits:16", label: "16 位数字", example: "1234567812345678" },
  { key: "hex16", template: "hex:16", label: "16 位十六进制", example: "a1b2c3d4e5f6a1b2" },
  { key: "letters6", template: "letters:6", label: "6 位纯字母", example: "abCdEf" },
  { key: "alnum8", template: "alnum:8", label: "8 位字母数字混合", example: "Ab3Xy7Qz" },
  { key: "username", template: "username", label: "用户名（字母开头 6-10 位）", example: "alice42" },
  { key: "timestamp", template: "timestamp", label: "Unix 毫秒时间戳", example: "1714665600000" },
];

// ─── 实体类型 ─────────────────────────────────────────────────────────

export interface TestDataItem {
  id: string;
  set_id: string;
  key: string;
  value_type: ValueType;
  description: string | null;
  sort_order: number;
  value_text: string | null;
  value_json: unknown;
  has_secret_value: boolean;
  file_path: string | null;
  file_size: number | null;
  file_mime: string | null;
  created_at: string;
  updated_at: string;
}

export interface TestDataSet {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  category: string | null;
  scope: DataSetScope;
  environment_id: string | null;
  owner_id: string | null;
  is_default: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  item_count: number;
}

export interface TestDataSetDetail extends TestDataSet {
  items: TestDataItem[];
}

export interface PaginatedSets {
  items: TestDataSet[];
  total: number;
  page: number;
  page_size: number;
}

export interface TestDataItemReveal {
  id: string;
  key: string;
  value_type: ValueType;
  value_text: string | null;
  value_secret: string | null;
  value_json: unknown;
}

// ─── 请求类型 ─────────────────────────────────────────────────────────

export interface SetCreateParams {
  name: string;
  description?: string | null;
  category?: string | null;
  scope: DataSetScope;
  environment_id?: string | null;
  is_default?: boolean;
}

export interface SetUpdateParams {
  name?: string;
  description?: string | null;
  category?: string | null;
  is_default?: boolean;
}

export interface ItemCreateParams {
  key: string;
  value_type: ValueType;
  description?: string | null;
  sort_order?: number;
  value_text?: string | null;
  value_secret?: string | null;
  value_json?: unknown;
}

export interface ItemUpdateParams {
  key?: string;
  description?: string | null;
  sort_order?: number;
  value_text?: string | null;
  value_secret?: string | null;
  value_json?: unknown;
  clear_value_text?: boolean;
  clear_value_secret?: boolean;
  clear_value_json?: boolean;
}

// ─── Task 8.6 增强：导入 / 克隆 / 推荐 / save-as-set 类型 ──────────────

/** 单条导入物料。与 ItemCreateParams 结构一致，但 file 类型不支持从导入通道创建 */
export interface ImportItem {
  key: string;
  value_type: ValueType;
  description?: string | null;
  sort_order?: number;
  value_text?: string | null;
  value_secret?: string | null;
  value_json?: unknown;
}

export type ImportMode = "skip_existing" | "upsert";

export interface ImportRequest {
  items: ImportItem[];
  mode?: ImportMode;
}

export interface ImportError {
  row: number;
  key: string | null;
  message: string;
}

export interface ImportReport {
  created: number;
  updated: number;
  skipped: number;
  errors: ImportError[];
  total: number;
}

export interface CloneRequest {
  new_name: string;
  description?: string | null;
  category?: string | null;
  scope?: DataSetScope | null;
  environment_id?: string | null;
  is_default?: boolean;
}

export interface SaveAsSetRequest {
  name: string;
  description?: string | null;
  category?: string | null;
  scope?: DataSetScope;
  environment_id?: string | null;
  overrides: ImportItem[];
}

export type RecommendReasonCode =
  | "env_default"
  | "project_default"
  | "testcase_default"
  | "personal"
  | "popular";

export interface RecommendedSet {
  set: TestDataSet;
  reason: string;
  reason_code: RecommendReasonCode;
}

export const RECOMMEND_REASON_META: Record<
  RecommendReasonCode,
  { label: string; color: "default" | "info" | "success" | "warning"; icon: string }
> = {
  env_default: {
    label: "环境默认",
    color: "success",
    icon: "i-carbon-cloud",
  },
  project_default: {
    label: "项目默认",
    color: "success",
    icon: "i-carbon-star-filled",
  },
  testcase_default: {
    label: "用例默认",
    color: "info",
    icon: "i-carbon-bookmark",
  },
  personal: {
    label: "我的物料",
    color: "warning",
    icon: "i-carbon-user-avatar",
  },
  popular: {
    label: "常用",
    color: "default",
    icon: "i-carbon-chart-line",
  },
};

// ─── API 方法（物料集）────────────────────────────────────────────────

export function listSetsApi(
  projectId: string,
  params: {
    scope?: DataSetScope;
    environment_id?: string;
    page?: number;
    page_size?: number;
  } = {},
) {
  const q = new URLSearchParams();
  if (params.scope) q.set("scope", params.scope);
  if (params.environment_id) q.set("environment_id", params.environment_id);
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  const qs = q.toString();
  return request<ApiResponse<PaginatedSets>>(
    `/projects/${projectId}/test-data-sets${qs ? `?${qs}` : ""}`,
  );
}

export function createSetApi(projectId: string, data: SetCreateParams) {
  return request<ApiResponse<TestDataSetDetail>>(
    `/projects/${projectId}/test-data-sets`,
    { method: "POST", body: data },
  );
}

export function getSetApi(setId: string) {
  return request<ApiResponse<TestDataSetDetail>>(`/test-data-sets/${setId}`);
}

export function updateSetApi(setId: string, data: SetUpdateParams) {
  return request<ApiResponse<TestDataSetDetail>>(`/test-data-sets/${setId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteSetApi(setId: string) {
  return request<ApiResponse<null>>(`/test-data-sets/${setId}`, {
    method: "DELETE",
  });
}

// ─── API 方法（物料条目）──────────────────────────────────────────────

export function listItemsApi(setId: string) {
  return request<ApiResponse<{ items: TestDataItem[] }>>(
    `/test-data-sets/${setId}/items`,
  );
}

export function createItemApi(setId: string, data: ItemCreateParams) {
  return request<ApiResponse<TestDataItem>>(`/test-data-sets/${setId}/items`, {
    method: "POST",
    body: data,
  });
}

export function updateItemApi(itemId: string, data: ItemUpdateParams) {
  return request<ApiResponse<TestDataItem>>(`/test-data-items/${itemId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteItemApi(itemId: string) {
  return request<ApiResponse<null>>(`/test-data-items/${itemId}`, {
    method: "DELETE",
  });
}

export function revealItemApi(itemId: string) {
  return request<ApiResponse<TestDataItemReveal>>(
    `/test-data-items/${itemId}/reveal`,
  );
}

/**
 * 上传 file 类型物料。走 multipart/form-data。
 * 返回新建的 TestDataItem。
 */
export function uploadFileItemApi(
  setId: string,
  params: { key: string; file: File; description?: string; sort_order?: number },
) {
  const form = new FormData();
  form.append("key", params.key);
  form.append("file", params.file);
  if (params.description) form.append("description", params.description);
  if (params.sort_order !== undefined) {
    form.append("sort_order", String(params.sort_order));
  }
  return request<ApiResponse<TestDataItem>>(
    `/test-data-sets/${setId}/items/upload`,
    { method: "POST", body: form },
  );
}

/** 构造下载 URL（含 token 头由 request 拦截器注入；直接开新 tab 会丢 token，
 *  所以下载文件时推荐用 fetch 再 save blob） */
export function fileItemDownloadUrl(itemId: string): string {
  return `/api/test-data-items/${itemId}/file`;
}

// ─── Task 8.6：导入 / 克隆 / 推荐 / save-as-set API ────────────────────

/**
 * JSON 批量导入物料。每条 item 结构与创建单条时一致（file 类型不支持走这里）。
 * HTTP 始终 200，部分失败细节在 `report.errors` 里。
 */
export function importItemsJsonApi(setId: string, payload: ImportRequest) {
  return request<ApiResponse<ImportReport>>(
    `/test-data-sets/${setId}/import`,
    { method: "POST", body: payload },
  );
}

/**
 * CSV 批量导入（multipart/form-data）。
 * - 必需列：``key``、``value_type``
 * - 可选列：``description`` / ``value_text`` / ``value_secret`` / ``value_json`` / ``sort_order``
 * - 列名支持中英文别名（详见后端 parse_csv_to_items 实现）
 */
export function importItemsCsvApi(
  setId: string,
  params: { file: File; mode?: ImportMode },
) {
  const form = new FormData();
  form.append("file", params.file);
  if (params.mode) form.append("mode", params.mode);
  return request<ApiResponse<ImportReport>>(
    `/test-data-sets/${setId}/import/csv`,
    { method: "POST", body: form },
  );
}

/**
 * 克隆物料集。file 类型的物理文件会被复制到新 set 目录，删源不会影响副本。
 * personal scope 且调用者不是 owner 时，service 会强制转成"克隆为我的私人副本"。
 */
export function cloneSetApi(setId: string, payload: CloneRequest) {
  return request<ApiResponse<TestDataSetDetail>>(
    `/test-data-sets/${setId}/clone`,
    { method: "POST", body: payload },
  );
}

/**
 * 推荐要加载的物料集（环境默认 / 项目默认 / 用例默认 / 个人 / 常用），
 * 用于执行前弹窗的"建议勾选"列表。``environment_id`` 传入后，会把当前
 * 环境的 ``default_data_set_ids`` 作为最高优先级推荐项一并返回（验收新增），
 * 让前端把环境默认集自动勾上、用户仍可取消。
 */
export function recommendSetsApi(
  projectId: string,
  params: {
    testcase_ids?: string[];
    environment_id?: string | null;
    top_n?: number;
  } = {},
) {
  const qs = new URLSearchParams();
  (params.testcase_ids ?? []).forEach((id) => qs.append("testcase_ids", id));
  if (params.environment_id) {
    qs.set("environment_id", params.environment_id);
  }
  if (params.top_n !== undefined) qs.set("top_n", String(params.top_n));
  const query = qs.toString();
  return request<ApiResponse<{ items: RecommendedSet[] }>>(
    `/projects/${projectId}/test-data/recommend${query ? `?${query}` : ""}`,
  );
}

/**
 * 把"执行弹窗里临时改的几个物料"沉淀为新物料集（默认 personal scope）。
 */
export function saveOverridesAsSetApi(
  projectId: string,
  payload: SaveAsSetRequest,
) {
  return request<ApiResponse<TestDataSetDetail>>(
    `/projects/${projectId}/test-data/save-as-set`,
    { method: "POST", body: payload },
  );
}

/**
 * 主动下载 file 物料（保留文件名、自动携带 token）。
 * 调用方不关心中间过程，成功后浏览器触发保存弹窗。
 */
export async function downloadFileItem(itemId: string, filename: string): Promise<void> {
  const token = localStorage.getItem("access_token") || "";
  const res = await fetch(fileItemDownloadUrl(itemId), {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    throw new Error(`下载失败：HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ─── 辅助函数 ─────────────────────────────────────────────────────────

/**
 * 在前端本地预览 random 模板的输出，避免"试运行"往返一次网络。
 * 字符集和分布与后端 random_generator.py 大致一致；细节上可能有小差异，
 * 但展示给用户看"大概长这样"足够。
 */
export function previewRandomTemplate(template: string): string {
  const tpl = template.trim().toLowerCase();
  const [prefix, rawParam = ""] = tpl.includes(":") ? tpl.split(":", 2) : [tpl, ""];
  const parseN = (v: string, fallback: number) => {
    const n = parseInt(v, 10);
    if (!Number.isFinite(n) || n <= 0) return fallback;
    return Math.min(n, 128);
  };
  const pick = (chars: string, n: number) =>
    Array.from({ length: n }, () =>
      chars.charAt(Math.floor(Math.random() * chars.length)),
    ).join("");
  const digits = "0123456789";
  const letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const lower = "abcdefghijklmnopqrstuvwxyz";
  switch (prefix) {
    case "phone": {
      const region = rawParam.toUpperCase() || "CN";
      if (region === "US") return "1" + pick(digits, 10);
      // CN
      const second = "3456789".charAt(Math.floor(Math.random() * 7));
      return `1${second}${pick(digits, 9)}`;
    }
    case "email": {
      const domain = rawParam || "example.com";
      return `${pick(lower, 7)}${pick(digits, 3)}@${domain}`;
    }
    case "uuid":
    case "uuid4":
      return crypto.randomUUID();
    case "digits":
      return pick(digits, parseN(rawParam, 8));
    case "hex":
      return pick("0123456789abcdef", parseN(rawParam, 16));
    case "letters":
      return pick(letters, parseN(rawParam, 6));
    case "alnum":
      return pick(letters + digits, parseN(rawParam, 8));
    case "username":
    case "name": {
      const n = rawParam ? parseN(rawParam, 8) : 6 + Math.floor(Math.random() * 5);
      return pick(lower, 1) + pick(lower + digits, n - 1);
    }
    case "timestamp":
      return String(Date.now());
    default:
      return template;
  }
}

/**
 * 把 bytes 转成人类可读大小（用于 file 物料展示）。
 */
export function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
