# 提示词管理系统设计 - 借鉴 WHartTest 并优化

## 一、WHartTest 提示词管理分析

### 1.1 它做对了什么

WHartTest 的提示词设计有几个值得借鉴的亮点：

**1. 类型化提示词**
不是简单的"一个大文本框"，而是按用途分类（通用对话、完整性分析、一致性分析、可测性分析等），让提示词有了明确的使用场景：

```python
# WHartTest 的提示词类型
PromptType.GENERAL              # 通用对话
PromptType.COMPLETENESS_ANALYSIS # 完整性分析
PromptType.TESTABILITY_ANALYSIS  # 可测性分析
PromptType.CLARITY_ANALYSIS      # 清晰度分析
# ... 等 9 种类型
```

**2. 程序调用与用户选择分离**
区分了"人工选择的提示词"和"程序自动调用的提示词"。需求评审时系统自动按类型查找对应提示词，不需要用户手动选。

**3. 三级优先级系统**
对话时提示词的生效规则清晰：用户指定 > 用户默认 > 全局 LLM 配置。

**4. 对话头部选择器**
在对话界面的顶部可以直接选择提示词，切换对话角色很方便。

### 1.2 它的问题

**1. 用户级而非项目级**
提示词绑定到用户而不是项目。但不同项目的测试对象差异很大（金融系统 vs 电商系统），同一个"可测性分析"提示词不能通用。

**2. 类型过于固定**
9 种类型硬编码在代码中，无法扩展。如果要新增一种分析维度，需要改代码、改数据库、改前端。

**3. 前端操作分散**
提示词管理在"设置"里，使用在"对话"和"评审"里，用户心智模型断裂——改完提示词不知道哪里生效了。

**4. 缺少版本管理**
修改提示词后旧版本丢失。无法回溯"上次评审用的是什么提示词"。

**5. 缺少效果反馈**
改了提示词不知道效果好不好，没有对比机制。

---

## 二、新平台提示词系统设计

### 2.1 设计理念

**"提示词是可共享的专家知识，不是个人配置"**

- 提示词归属**项目**，团队共享
- 支持自定义分类，不写死
- 内置优质模板，开箱即用
- 变量系统让提示词可复用
- 历史版本可追溯

### 2.2 数据模型

```sql
-- 提示词模板
CREATE TABLE prompt_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,

    -- 基本信息
    name VARCHAR(200) NOT NULL,
    description TEXT,
    content TEXT NOT NULL,                   -- 提示词正文，支持 {{变量}}

    -- 分类（灵活分类，不硬编码）
    category VARCHAR(50) NOT NULL,           -- chat, review, generation, ui_test, custom
    sub_category VARCHAR(50),                -- 例: review 下的 completeness, testability 等

    -- 使用配置
    is_system BOOLEAN DEFAULT FALSE,         -- 系统内置模板（不可删，可覆盖）
    is_default BOOLEAN DEFAULT FALSE,        -- 该分类下的默认
    auto_apply BOOLEAN DEFAULT FALSE,        -- 是否由程序自动调用（无需手动选择）

    -- 变量声明
    variables JSONB DEFAULT '[]',            -- 声明变量列表及其说明
    -- 例: [{"name": "doc_content", "label": "文档内容", "source": "auto"},
    --       {"name": "project_name", "label": "项目名", "source": "context"}]

    -- 元数据
    version INTEGER DEFAULT 1,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 提示词版本历史
CREATE TABLE prompt_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID REFERENCES prompt_templates(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    change_note VARCHAR(500),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 提示词使用记录（可选，用于效果追踪）
CREATE TABLE prompt_usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID REFERENCES prompt_templates(id) ON DELETE SET NULL,
    template_version INTEGER,
    context VARCHAR(50) NOT NULL,            -- chat, review, testcase_gen, ui_test
    reference_id UUID,                       -- 关联的对象（会话/评审/用例批次）
    tokens_used INTEGER,
    user_rating SMALLINT,                    -- 用户反馈 1-5（可选）
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 2.3 分类体系

不同于 WHartTest 写死 9 种类型，新平台用**二级分类**，可扩展：

| category | sub_category | 说明 | 自动调用 |
|----------|-------------|------|----------|
| `chat` | `general` | 通用对话 | 否，用户选择 |
| `chat` | `testing_expert` | 测试专家角色 | 否，用户选择 |
| `chat` | `code_reviewer` | 代码评审角色 | 否，用户选择 |
| `review` | `completeness` | 完整性分析 | 是 |
| `review` | `testability` | 可测性分析 | 是 |
| `review` | `clarity` | 清晰度分析 | 是 |
| `review` | `risk` | 风险识别 | 是 |
| `generation` | `functional` | 功能测试用例生成 | 是 |
| `generation` | `boundary` | 边界值测试用例生成 | 是 |
| `ui_test` | `translator` | 用例翻译为 Playwright | 是（二期） |
| `ui_test` | `recovery` | 失败恢复分析 | 是（二期） |
| `custom` | 用户自定义 | 任意场景 | 否 |

用户可以新增 `custom` 类型的提示词，也可以覆盖系统内置的任何提示词。

### 2.4 变量系统

提示词不是死文本，支持 `{{变量名}}` 占位符，运行时自动填充：

```
# 示例：需求评审提示词模板

你是一位资深的软件质量专家，正在评审 {{project_name}} 项目的需求文档。

## 需求文档内容
{{doc_content}}

## 评审要求
请从完整性角度分析以上需求，重点关注：
1. 功能需求是否有遗漏
2. 非功能需求是否覆盖
3. 边界条件是否考虑

## 输出格式
{{output_format}}
```

**变量来源**：

| 来源 | 说明 | 示例 |
|------|------|------|
| `context` | 系统自动注入 | `project_name`, `current_date`, `user_name` |
| `auto` | 触发时自动传入 | `doc_content`, `testcase_steps` |
| `manual` | 用户每次手动填 | `focus_area`, `extra_requirements` |

### 2.5 与各模块的集成方式

#### 对话模块

```
┌─────────────────────────────────────────────────────┐
│  [DeepSeek ▼]  [提示词: 测试专家 ▼]  [⚙️系统提示词]   │
├─────────────────────────────────────────────────────┤
│                                                     │
│  💬 对话内容...                                      │
│                                                     │
```

- 顶部下拉选择 `category=chat` 的提示词
- 选择后作为 system prompt 注入对话
- 切换提示词 → 新的对话上下文（保留历史但角色改变）

**优先级**：用户选择 > 项目默认 > 无提示词

#### 需求评审

用户点击"AI 评审"时，系统自动查找该项目下 `category=review` 且 `auto_apply=true` 的所有提示词，逐维度评审：

```python
async def review_document(project_id, document_id):
    review_prompts = await get_prompts(
        project_id=project_id,
        category="review",
        auto_apply=True
    )
    # 找到: completeness, testability, clarity, risk 四个提示词

    results = []
    for prompt in review_prompts:
        filled_content = render_template(prompt.content, {
            "doc_content": document.content_text,
            "project_name": project.name,
            "output_format": REVIEW_JSON_FORMAT,
        })
        result = await llm.chat([{"role": "system", "content": filled_content}])
        results.append({"dimension": prompt.sub_category, "result": result})

    return results
```

**用户不需要在评审时选择提示词**——系统自动找到所有评审类提示词并执行。

#### 用例生成

同理，`category=generation` 的提示词自动参与用例生成。

#### UI 测试（二期）

`category=ui_test` 的提示词控制 AI 如何翻译用例和处理失败。

### 2.6 内置模板（开箱即用）

系统初始化时自动创建一组高质量内置模板，用户可以在此基础上修改：

```python
BUILT_IN_PROMPTS = [
    # ===== 对话角色 =====
    {
        "name": "测试专家",
        "category": "chat",
        "sub_category": "testing_expert",
        "is_system": True,
        "content": """你是一位有10年经验的软件测试专家。你擅长：
- 从用户视角发现功能缺陷
- 设计全面的测试策略
- 评估软件质量风险
当前项目：{{project_name}}
请用专业但易懂的语言与用户交流。"""
    },

    # ===== 需求评审 =====
    {
        "name": "完整性分析",
        "category": "review",
        "sub_category": "completeness",
        "auto_apply": True,
        "is_system": True,
        "is_default": True,
        "variables": [
            {"name": "doc_content", "label": "文档内容", "source": "auto"},
            {"name": "project_name", "label": "项目名", "source": "context"},
        ],
        "content": """你是需求评审专家。请对以下需求文档进行完整性分析。
...（完整 prompt 内容）..."""
    },

    {
        "name": "可测性分析",
        "category": "review",
        "sub_category": "testability",
        "auto_apply": True,
        "is_system": True,
        "is_default": True,
        "content": "..."
    },

    # ===== 用例生成 =====
    {
        "name": "功能测试用例生成",
        "category": "generation",
        "sub_category": "functional",
        "auto_apply": True,
        "is_system": True,
        "is_default": True,
        "content": "..."
    },
]
```

### 2.7 前端交互设计

#### 提示词管理页（设置 > 提示词）

```
┌─────────────────────────────────────────────────────────────┐
│ 提示词管理                        [+ 新建提示词]              │
├──────────┬──────────────────────────────────────────────────┤
│          │                                                  │
│ 分类筛选  │  名称            分类         自动调用  默认  操作  │
│          │  ──────────────────────────────────────────────  │
│ 全部 (12) │  🔒测试专家      对话/通用      否      是    编辑  │
│ 对话 (3)  │   代码评审官     对话/自定义     否      否    编辑  │
│ 评审 (4)  │  🔒完整性分析    评审/完整性     是      是    编辑  │
│ 生成 (2)  │  🔒可测性分析    评审/可测性     是      是    编辑  │
│ UI测试(2) │  🔒功能用例生成  生成/功能       是      是    编辑  │
│ 自定义(1) │   安全测试补充   自定义         否      否    编辑  │
│          │                                                  │
│          │  🔒 = 系统内置（可编辑内容，不可删除）              │
└──────────┴──────────────────────────────────────────────────┘
```

#### 提示词编辑器

```
┌─────────────────────────────────────────────────────────────┐
│ 编辑提示词: 完整性分析                        [保存] [取消]   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ 名称: [完整性分析                         ]                  │
│ 分类: [评审 ▼]  子分类: [completeness ▼]                     │
│ 描述: [对需求文档进行完整性维度分析        ]                  │
│                                                             │
│ ☑ 自动调用（程序触发时自动使用，无需手动选择）                 │
│ ☑ 设为该分类默认                                             │
│                                                             │
│ 可用变量:  {{project_name}}  {{doc_content}}  {{output_format}}│
│           点击变量可插入到光标位置                             │
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 你是需求评审专家。请对 {{project_name}} 项目的以下需求    │ │
│ │ 文档进行完整性分析。                                     │ │
│ │                                                         │ │
│ │ ## 需求文档                                              │ │
│ │ {{doc_content}}                                          │ │
│ │                                                         │ │
│ │ ## 分析要求                                              │ │
│ │ 请从以下维度评估完整性：                                  │ │
│ │ 1. 功能需求是否有遗漏                                    │ │
│ │ 2. 非功能需求（性能/安全/可用性）是否覆盖                  │ │
│ │ 3. 边界条件和异常场景是否考虑                              │ │
│ │ ...                                                     │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ 版本: v3  [查看历史版本 ▼]                                   │
│ 上次修改: 2026-04-30 by admin                                │
└─────────────────────────────────────────────────────────────┘
```

### 2.8 对话中的提示词选择

对话顶部集成一个轻量选择器，不需要跳到设置页：

```
┌──────────────────────────────────────────────────────┐
│ [DeepSeek-Chat ▼]  [🎭 测试专家 ▼]                    │
│                     ├─ 💬 通用对话                     │
│                     ├─ 🧪 测试专家  ← 当前            │
│                     ├─ 📝 代码评审官                   │
│                     ├─ ➕ 自定义...                    │
│                     └─ ⚙️ 管理提示词                   │
├──────────────────────────────────────────────────────┤
```

---

## 三、与 WHartTest 的对比总结

| 维度 | WHartTest | 新平台 |
|------|-----------|--------|
| 归属 | 用户级 | **项目级**（团队共享） |
| 分类 | 9种硬编码类型 | **二级分类 + 自定义**（灵活扩展） |
| 调用方式 | 手动选择 or 按类型查询 | **auto_apply 标记**（自动与手动统一管理） |
| 变量 | 无 | **{{变量}} 模板系统**（可复用） |
| 版本 | 无 | **版本历史 + 变更记录** |
| 内置模板 | 需用户手动创建 | **开箱即用 + 可覆盖** |
| 效果追踪 | 无 | **使用记录 + 可选评分** |
| 编辑体验 | 普通文本框 | **变量点击插入 + 预览** |
| 优先级 | 用户指定>默认>全局 | **用户选择 > 项目默认 > 内置模板** |

---

## 四、实现计划补充

提示词管理融入一期计划中，不单独作为二期。具体嵌入位置：

| 原计划步骤 | 补充内容 |
|-----------|---------|
| Task 3.1 (LLM 配置后端) | 同时创建 PromptTemplate 模型和 CRUD API |
| Task 3.3 (LLM 配置前端) | 同时创建提示词管理页面 |
| Task 3.4 (对话前端) | 对话顶部集成提示词选择器 |
| Task 4.2 (AI 评审后端) | 评审时自动查找 review 类提示词 |
| Task 5.2 (AI 生成用例后端) | 生成时自动查找 generation 类提示词 |
| Task 6.3 (部署) | 初始化脚本创建内置提示词模板 |

**额外增加 1 个 Task**：

### Task 3.1b - 提示词管理后端

**产出**：
- `app/modules/prompts/models.py` — PromptTemplate, PromptVersion, PromptUsageLog
- `app/modules/prompts/schemas.py`
- `app/modules/prompts/service.py` — CRUD + 模板渲染 + 版本管理
- `app/modules/prompts/router.py` — API 端点
- `app/modules/prompts/built_in.py` — 内置模板数据
- 初始化命令：`init_prompts`

---

*文档版本：v1.0*
*最后更新：2026-04-29*
