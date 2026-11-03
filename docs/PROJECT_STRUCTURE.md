# 当前项目结构

更新日期：2026-11-03。根目录：D:/workspace/packaging-design-agent。

```text
backend/app/
  main.py                       项目、对话任务、事件与图片接口
  agent/instructions.py         唯一的图像主控SYSTEM指令
  agent/graph.py                通用模型 / 工具循环，检查点只存控制状态
  agent/observations.py         将真实图片保存为可重建的视觉消息引用
  session/journal.py            会话事件追加、幂等key、序号及公开/私有读边界
  session/projection.py         活跃消息投影、摘要替换与工具批次配对
  session/migration.py          一次性导入旧事件与检查点，完成后删除旧表
  context/builder.py            从session准备并记录每一次模型请求
  context/compaction.py         较早消息摘要、保护当前输入与完整工具批次
  context/meter.py              文本与图片token估算、输入预算
  context/images.py             内容摘要预览引用、校验与发送前还原
  tools/base.py                 工具定义及执行依赖的数据契约
  tools/registry.py             统一注册工具，生成模型参数定义和执行映射
  tools/project.py              read_project实现与定义
  tools/images.py               generate_image、edit_image、inspect_image实现与定义
  tools/user.py                 ask_user实现与定义
  runtime/gateway.py            工具分发、调用记录、预算、幂等与错误处理
  runtime/results.py            任务终态、最终图片选择及交付记录
  runtime/worker.py             持久化执行、等待和恢复
  providers/deepseek.py         多模态主控适配
  providers/seedream.py         实际生图与参考编辑，可选尺寸
  providers/ocr.py              按需本地文字识别
  persistence/repositories.py   SQLite事务与项目、任务、版本存取
  persistence/assets.py         图片文件、版本记录保存与归属、路径、完整性校验
  persistence/projects.py       项目删除、检查点清理、共享文件保护和回收重试
  persistence/locking.py        worker、项目与预览的进程间锁
  domain/models.py              项目、图像请求、思考强度与用户回答的数据结构
  domain/projects.py            Agent与工具共用的已知项目事实
frontend/src/features/workspace/
  Workspace.tsx                 两栏布局、完整对话、参考图与图片交付
  ProjectSidebar.tsx            项目选择、搜索、删除入口及空状态
  DeleteProjectDialog.tsx       删除确认、错误与进行中保护
  Dialog.tsx                    新建/删除弹窗的焦点管理与键盘操作
  Icon.tsx                      工作区共用图标
  AgentTrace.tsx                trace节点渲染、工具结果与自动收起
  traceProjection.ts            事件去重与工具、说明、压缩节点纯投影
  useSessionEvents.ts           按session订阅、断线接续与更新任务快照
  ReasoningSelector.tsx          off/low/high/max选择控件
  useReasoningEffort.ts          浏览器偏好保存与服务默认值读取
backend/tests/
  test_image_agent.py           15项图像、参考、OCR、API与交付检查
  test_runtime.py               预算、幂等、等待与重启恢复检查
  test_deepseek.py              思考强度、摘要请求、截断及窗口溢出识别
  test_reasoning_effort.py      7项四档请求、任务持久化及默认值检查
  test_sessions.py              session投影、压缩、迁移、请求重建及恢复
  test_project_deletion.py      删除级联、共享图片、路径边界、并发及回收重试
frontend/tests/
  traceProjection.test.ts       历史与实时事件一致、去重、恢复及压缩节点
scripts/
  setup.ps1 / start.ps1 / stop.ps1
  worker_status.py               后台执行器状态
  setup-ocr.ps1 / download_ocr_wheel.py / ocr_service.py
  verify_session_workspace.py    隔离会话、压缩、删除和桌面/手机页面回归
docs/                            当前架构、验收、演示和开源调研
data/                            本地SQLite、检查点、图片与OCR缓存（忽略）
outputs/image-agent/              原始真实调用与页面证据（忽略）
outputs/cleanup/                  本次清理清单与验证结果（忽略）
outputs/modularization/           模块化回归与数据保留验证（忽略）
outputs/reasoning-effort/          思考强度单测、隔离页面联调与真实off请求（忽略）
outputs/session-context/           自动压缩、trace与恢复的隔离页面结果（忽略）
outputs/project-management/        本机工作区、删除确认及手机侧栏的只读验证（忽略）
```

当前源码只有图像生成与编辑这一条产品路径，没有旧模式的兼容分支。图片版本仍用于保存编辑父子关系，不再承担可编辑文档、母版审批或印刷导出职责。已有项目记录和生成图片保存在data中，不属于应删除的代码。

## 模块边界

模型通过tools/registry提供的参数定义选择工具；agent/graph把调用交给runtime/gateway，网关查找同一注册表中的执行函数。工具在tools中实现具体操作，供应商通信留在providers，文件和数据访问留在persistence。真实图片由agent/observations保存引用，context在发送模型请求时还原。

session_events统一保存公开事实及私有模型消息。context按事件序号重建活跃模型输入，必要时追加摘要替换记录；前端从公开事件独立投影trace。LangGraph检查点不再保存另一份messages，前端也不承担模型上下文裁剪。请求准备记录通过through_seq关联可重放的会话快照。

生成与编辑工具通过ToolContext.submit_image提交请求，网关在远端调用前记录提交并检查预算。ask_user通过同一网关执行；中断信号交给LangGraph和worker保存检查点，恢复时复用同一条工具记录。任务交付与终态记录由runtime/results负责。

工具说明、参数及执行函数在对应工具模块中定义，再由tools/registry统一注册。新增工具时添加实现及注册项即可，graph和gateway没有按工具名逐个判断的业务分支。

思考强度是每轮任务的配置：前端选择后提交到RunCreate，API校验并保存reasoning_effort，agent将它随每次决策传给DeepSeek适配器。适配器负责把off映射为关闭思考，其余档位映射为启用思考和对应强度，不修改共享Settings。等待恢复读取任务中保存的原值。
