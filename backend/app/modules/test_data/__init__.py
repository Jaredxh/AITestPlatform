"""测试物料模块（二期 Task 8.5）。

将"用例步骤描述做什么"和"具体要用什么数据"解耦：
- ``TestDataSet`` 是一组 ``TestDataItem`` 的集合（物料集），按 scope
  落在 project / environment / personal 三级。
- ``TestDataItem`` 是一条具体物料条目，支持 6 种 ``value_type``：
  string / secret / multiline / file / random / dataset。

本 task 只实现"建表 + 基础 CRUD + Fernet 加密 + 文件上传 + reveal"。
后续 task 的职责：
- Task 8.6：CSV/JSON 导入、clone、recommend、save-as-set
- Task 8.7：前端管理页 + 绑定入口
- Task 9.1：五级合并 resolver + 模板预替换
- Task 9.2：``platform_get_secret`` / ``platform_get_file`` 等 tool
- Task 9.3：缺料 preflight + missing-check 端点
"""
