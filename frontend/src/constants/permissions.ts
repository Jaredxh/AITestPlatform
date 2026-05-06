/**
 * 权限元数据：把后端权限 key 按"菜单"维度组织成中文树形结构。
 * - 菜单节点本身（key = 形如 "menu:<scope>"）作为「赋予该菜单全部权限」的快捷开关；
 *   勾选菜单 = 自动选中菜单下所有子权限；不勾选 = 该角色看不到此菜单。
 * - 子节点直接对应后端 Permissions.* 字符串。
 */

export interface PermissionLeaf {
  key: string;
  label: string;
  description?: string;
}

export interface PermissionGroup {
  /** 内部分组 key（用于"勾选菜单 = 全选子项"的本地标记，不会下发到后端） */
  key: string;
  /** 菜单中文名 */
  label: string;
  /** 关联前端导航菜单（路由 name），勾选则该角色可见此菜单 */
  routeNames?: string[];
  /** 描述 */
  description?: string;
  /** 子权限列表（后端 Permissions 枚举值） */
  permissions: PermissionLeaf[];
}

export const PERMISSION_GROUPS: PermissionGroup[] = [
  {
    key: "menu:project",
    label: "项目管理",
    routeNames: ["ProjectList", "ProjectSettings"],
    description: "管理产品 / 业务项目空间及成员",
    permissions: [
      { key: "project:view", label: "查看项目", description: "浏览项目列表与详情" },
      { key: "project:create", label: "创建项目" },
      { key: "project:edit", label: "编辑项目", description: "修改项目信息、成员等" },
      { key: "project:delete", label: "删除项目" },
    ],
  },
  {
    key: "menu:requirement",
    label: "需求管理",
    routeNames: ["RequirementList", "RequirementDetail"],
    description: "上传需求文档并使用 AI 评审",
    permissions: [
      { key: "requirement:view", label: "查看需求文档" },
      { key: "requirement:upload", label: "上传文档" },
      { key: "requirement:delete", label: "删除文档" },
      { key: "requirement:review", label: "发起 AI 评审" },
    ],
  },
  {
    key: "menu:testcase",
    label: "测试用例",
    routeNames: ["TestcaseList"],
    description: "组织、编辑及生成测试用例",
    permissions: [
      { key: "testcase:view", label: "查看用例" },
      { key: "testcase:create", label: "新建用例" },
      { key: "testcase:edit", label: "编辑用例" },
      { key: "testcase:delete", label: "删除用例" },
      { key: "testcase:generate", label: "AI 生成用例" },
      { key: "testcase:approve", label: "审核 / 接受用例" },
    ],
  },
  {
    key: "menu:chat",
    label: "AI 对话",
    routeNames: ["AIChat"],
    description: "使用平台内置的 AI 对话能力",
    permissions: [{ key: "llm:chat", label: "使用 AI 对话" }],
  },
  {
    key: "menu:ui-automation",
    label: "UI 自动化",
    routeNames: ["UIEnvironmentList", "UIEnvironmentListForProject"],
    description: "维护执行环境、登录态与前置步骤",
    permissions: [
      { key: "ui_env:view", label: "查看 UI 环境" },
      { key: "ui_env:create", label: "新建 UI 环境" },
      { key: "ui_env:edit", label: "编辑 UI 环境", description: "包括前置步骤、凭据、登录态清除" },
      { key: "ui_env:delete", label: "删除 UI 环境" },
    ],
  },
  {
    key: "menu:test-data",
    label: "测试物料",
    routeNames: ["TestDataView", "TestDataViewGlobal", "TestDataSetEditor"],
    description: "管理可复用的测试数据：账号、文件、参数化数据集",
    permissions: [
      { key: "test_data:view", label: "查看物料集" },
      { key: "test_data:edit", label: "编辑物料集", description: "创建 / 更新 / 删除集合与条目（不含文件上传）" },
      { key: "test_data:import", label: "批量导入与文件上传", description: "CSV / JSON 导入、物料文件上传、克隆物料集" },
      { key: "test_data:reveal", label: "查看密文明文", description: "读取 secret 类型物料的明文（每次调用都记录审计日志）" },
    ],
  },
  {
    key: "menu:llm-config",
    label: "LLM 配置",
    routeNames: ["LLMConfig"],
    description: "管理大模型供应商与默认参数",
    permissions: [
      { key: "llm:config", label: "管理 LLM 配置", description: "增删改与测试连接" },
    ],
  },
  {
    // 历史上提示词路由复用 REQUIREMENT_* 权限，导致这里没有独立分组——
    // 用户编辑角色时找不到"提示词管理"开关（2026-05 验收反馈）。后端已经
    // 拆出 ``PROMPT_*`` 三个权限并通过 ``init_data._seed_roles`` 自动下发
    // 到系统角色；前端这里同步加分组，让角色权限树能展示。
    key: "menu:prompt",
    label: "提示词管理",
    routeNames: ["PromptManagement"],
    description: "维护项目级 LLM 提示词模板与版本历史",
    permissions: [
      { key: "prompt:view", label: "查看提示词", description: "浏览模板列表 / 详情 / 版本历史" },
      {
        key: "prompt:edit",
        label: "编辑提示词",
        description: "新建 / 修改 / 设为默认 / 初始化内置模板",
      },
      { key: "prompt:delete", label: "删除提示词", description: "系统内置模板不可删除" },
    ],
  },
  {
    key: "menu:user",
    label: "用户管理",
    routeNames: ["UserManagement"],
    description: "管理平台账户、状态与角色绑定",
    permissions: [{ key: "user:manage", label: "管理用户", description: "查看 / 新建 / 编辑 / 删除 / 改密" }],
  },
  {
    key: "menu:role",
    label: "角色管理",
    routeNames: ["RoleManagement"],
    description: "维护角色及其权限",
    permissions: [{ key: "role:manage", label: "管理角色", description: "新建 / 编辑 / 删除角色与权限分配" }],
  },
];

/**
 * 全部权限 key 的中文映射，用于角色列表里把英文权限渲染成中文短标签。
 */
export const PERMISSION_LABEL_MAP: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const group of PERMISSION_GROUPS) {
    for (const p of group.permissions) {
      map[p.key] = `${group.label} / ${p.label}`;
    }
  }
  return map;
})();

/**
 * 将一组权限 key 转换为"菜单维度"的中文标签数组（去重，按菜单分组聚合）。
 * 用于角色列表 / 详情的权限简洁展示。
 */
export function summarizePermissions(permissionKeys: string[]): { menu: string; full: boolean; count: number; total: number }[] {
  const set = new Set(permissionKeys);
  return PERMISSION_GROUPS.map((g) => {
    const total = g.permissions.length;
    const count = g.permissions.filter((p) => set.has(p.key)).length;
    return { menu: g.label, full: count === total && total > 0, count, total };
  }).filter((s) => s.count > 0);
}
