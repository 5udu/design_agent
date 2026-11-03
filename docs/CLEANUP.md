# 图像 Agent 代码清理

日期：2026-11-03。项目只保留对话式图像生成与参考编辑。

## 移除

- LEGACY_SYSTEM、LEGACY_TOOLS以及按项目模式选择旧实现的分支。
- 分层文档模型、模板、SVG渲染、Logo锁定、图层修改、母版选择与恢复、文档比较和导出服务及接口。
- 旧包装参考索引、专用评测路由与evals程序、三天计划验证和打包脚本。
- 对应旧功能的18项测试、重复的历史文档与旧界面截图。原test_day1中的9项运行时测试移至test_runtime，按当前图像工具验证。
- 已无用途的mode、包装模板、模板修复次数、目标文档版本等请求或配置字段；前端不再发送这些参数。

共删除47个旧源码、脚本、文档或示例图片文件，其中44个文本文件约3200行。没有将旧实现挪入另一个可运行的兼容目录。

## 保留

- 一份SYSTEM、五个工具、通用模型与工具循环。
- 图像完整性与项目归属检查；后续模块化整理至persistence/assets.py，供API、工具和视觉观察共用。OCR仍为按需能力。
- 原图、参考图片关系与父版本记录；版本仅用于图片追溯。
- 持久化任务、事件、检查点、询问恢复、预算及防重复提交。
- 已有项目、任务和生成图片；data和outputs中的本地运行资料不作为旧源码删除。

## 验证

- 27项当前功能离线测试：15项图像与API、9项运行时、3项主模型思考模式。
- 新增7项回归覆盖图片篡改、越界路径、OCR不可用、指定旧参考及清除参考、远端超时不重发、API范围和已有项目元数据不切换模式。
- 前端生产构建、已有真实对话页面重放及重启后的项目/资产/任务保留检查。
- 本次清理没有新增付费图像请求；此前的真实饭盒生成与改色记录仍可查看。

删除清单及机器验证结果保存在outputs/cleanup。当前结构见[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)，质量边界见[ACCEPTANCE.md](ACCEPTANCE.md)。

后续模块化已将五个工具实现集中到tools，注册、网关、主控、视觉消息和结果保存各自分工。该次模块化共28项测试，新增同一轮先生成后询问的中断恢复回归；阶段结果位于outputs/modularization，后续当前验收见ACCEPTANCE.md。

## Session、Context与trace改造后的清理

- 删除API中“最近六轮”conversation_context组装，read_project不再返回重复聊天历史。
- 删除AgentState中的完整messages和旧turns字段，控制检查点统一使用steps，模型输入从session构建。
- 删除按run的SSE接口与前端run订阅切换，统一按session游标接续。
- 删除AgentTrace内直接筛选原始事件、按文案比较去重的逻辑，使用纯事件投影和显式phase。
- 工具参数解析归入网关；图片视觉消息只保留一个生成入口，持久引用在发送前还原。
- MAX_MODEL_TURNS配置更名为MAX_MODEL_STEPS，.env.example和本机配置同步。
- 旧events表在成功迁移后删除；一次性导入模块仍负责保护已有历史及旧等待检查点，不保留另一条可运行Agent流程。

保留已有项目、任务、图片及历史证据。当前验证为后端81项、前端6项、生产构建及隔离页面回归；会话回归证据在outputs/session-context，本轮没有付费请求。

项目管理调整将侧栏、共用图标和弹窗焦点逻辑从Workspace提取，删除原来内联的侧栏与图标实现；worker与删除共用文件锁，移除重复的worker锁代码。移除服务启动时自动补默认项目的逻辑，使最后一个项目删除后重启仍为空工作区。新增删除和UI验证均使用隔离数据，没有批量删除本机已有项目。

## 本次仓库复查

- 删除scripts/verify_image_workspace.py：它仍使用旧trace和旧画布选择器；当前页面验证统一由隔离脚本完成，既有真实调用证据继续保留。
- 删除SessionJournal.event兼容包装和按run全局事件ID增量读取参数，统一使用append追加、session内seq接续；删除只覆盖旧游标接口的1项测试。
- 删除AgentState中没有读取方的session_cursor及其查询、赋值，删除PreparedContext中重复返回的through_seq；request_prepared内的请求边界记录继续保留。
- 删除前端未展示的savedTokens计算和没有对应样式或读取方的is-running class。
- 合并CSS中多轮覆盖的输入框、项目列表、参考图、弹窗和颜色规则；删除旧trace-event small规则。共减少21条规则、32个声明，并整理为可逐条检查的样式。
- DeepSeek凭据检查集中到共用请求入口，移除重复检查；TypeScript构建启用noUnusedLocals和noUnusedParameters。
- 核查全部Python源码、前端引用和依赖链；没有确认可移除的依赖，不根据源码未直接import就删除框架或OCR的传递依赖。

旧数据一次性迁移、LangGraph恢复、可选OCR安装和服务脚本仍有实际入口，继续保留。本机数据以本次复查开始时的快照为准，清理和验证不删除现有项目、图片、虚拟环境或历史运行资料。源代码备份、变更清单、样式对比和数据核对结果记录在outputs/repo-cleanup。
