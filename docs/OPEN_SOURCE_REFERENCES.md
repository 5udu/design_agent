# 生图 / 设计 Agent 的源码调研依据

核查日期：2026-11-02。只读查看官方 GitHub、源码和文档，没有安装、运行或复现效果。GitHub 连接器返回不可用，核查改用公开仓库与原始源文件。

本文区分论文设计、README 宣称和公开代码中能确认的实现。引用默认分支是调研时入口，后续真正复用代码时需要固定 commit 并复核许可。

## 1. T2I-Copilot：工具选择与真实图片反馈

项目：[SHI-Labs/T2I-Copilot](https://github.com/SHI-Labs/T2I-Copilot)。

源码能确认的机制：

- [main.py：工具注册和选择说明](https://github.com/SHI-Labs/T2I-Copilot/blob/master/main.py#L719-L740)提供生成 / 编辑工具描述。
- [main.py：反馈进入下一轮选择](https://github.com/SHI-Labs/T2I-Copilot/blob/master/main.py#L961-L1037)让模型结合目标与反馈选择工具及参数。
- [main.py：视觉评价与继续执行](https://github.com/SHI-Labs/T2I-Copilot/blob/master/main.py#L1453-L1582)读取真实图片，决定结束或继续尝试。
- [main.py：高层 StateGraph](https://github.com/SHI-Labs/T2I-Copilot/blob/master/main.py#L1605-L1622)节点相对固定，内部存在模型驱动的工具决策。这说明不能只看框图有无固定箭头来判断 Agent。

对新项目的启发：工具用途清楚，反馈和真实图像影响下一轮操作；生成、编辑和区域标注是不同能力；循环具有上限。

边界：公开 demo 的工具种类有限，没有包装品牌、精确文字、版式文档和业务版本能力。默认环境需要较重 GPU 配置，存在全局配置和终端输入等演示代码。我们学习控制机制，独立接入图像服务与产品运行时。

许可：[根仓库 MIT](https://github.com/SHI-Labs/T2I-Copilot/blob/master/LICENSE)；底层模型与其他资源单独核查。

## 2. GenArtist：生成与编辑工具的统一调度

项目：[zhenyuw16/GenArtist](https://github.com/zhenyuw16/GenArtist)。

源码能确认的机制：

- [demo_t2i.py：生成工具选择和布局](https://github.com/zhenyuw16/GenArtist/blob/main/demo_t2i.py#L111-L176)由 MLLM 选择生成操作与参数。
- [demo_t2i.py：观察后纠错](https://github.com/zhenyuw16/GenArtist/blob/main/demo_t2i.py#L178-L228)把生成图片与对象位置交回 MLLM，再生成编辑命令并分派工具。
- [agent_tool.py](https://github.com/zhenyuw16/GenArtist/blob/main/agent_tool.py)主要展开与执行命令；只查看这个文件会漏掉 demo 中的模型决策。

对新项目的启发：生成、分割、对象移除、替换和属性修改应作为有不同用途的工具；模型可以依据实际结果选择纠错动作。

边界：公开演示主要是生成后的一次观察纠错，不能当成成熟的持续自主运行时。脚本调用、共享文件和 `eval()` 等方式不适合作为产品工具网关；缺少项目隔离、任务恢复和版本控制。

许可：核查时未在根仓库发现明确 LICENSE，不推断为 MIT 或可自由复用。当前只借鉴机制；真正复制代码前需取得明确授权依据。

## 3. Jaaz：动态工具调用和画布状态

项目：[11cafe/jaaz](https://github.com/11cafe/jaaz)。

源码能确认的机制：

- [agent_manager.py](https://github.com/11cafe/jaaz/blob/main/server/services/langgraph_service/agent_manager.py#L93-L99)创建工具调用 Agent。
- [agent_service.py](https://github.com/11cafe/jaaz/blob/main/server/services/langgraph_service/agent_service.py#L103-L132)组织 planner 和创作 Agent，并沿用活跃 Agent 继续交互。
- [工具 schema 示例](https://github.com/11cafe/jaaz/blob/main/server/tools/generate_image_by_gpt_image_1_jaaz.py#L8-L42)包含 prompt、画幅、参考图 ID 等明确参数。
- [image_generation_core.py](https://github.com/11cafe/jaaz/blob/main/server/tools/utils/image_generation_core.py#L74-L97)保存素材到画布，返回 Markdown 图片 URL。

对新项目的启发：把用户选中素材、参考 ID 和画布状态与工具调用连接；工具按能力注册，由模型选择。

边界：已核查的生成结果路径没有证明图片像素自动回到主模型完成视觉评价。聊天和画布保存也不能证明运行中的任务可崩溃恢复；[所查 compile 路径](https://github.com/11cafe/jaaz/blob/main/server/services/langgraph_service/StreamProcessor.py#L28-L35)没有配置持久 checkpointer。

许可：[自定义社区 / 商业双许可](https://github.com/11cafe/jaaz/blob/main/LICENSE)。源码修改、衍生开发、再分发等受到商业许可限制，不作为新项目直接 fork 改造的底座。

## 4. Open-AI-Design-Agent：公开代码与外部服务边界

项目：[Anil-matcha/Open-AI-Design-Agent](https://github.com/Anil-matcha/Open-AI-Design-Agent)。

README 描述自主规划与模型路由，但公开的关键请求路径为：

- [creative_agent_router.py](https://github.com/Anil-matcha/Open-AI-Design-Agent/blob/main/server/app/routers/creative_agent_router.py#L12-L121)：chat、session、job、status 和 events 等转给代理函数。
- [muapi_helper.py](https://github.com/Anil-matcha/Open-AI-Design-Agent/blob/main/server/app/utils/muapi_helper.py#L12-L39)：默认发往外部 Muapi 服务。
- [CreativeCanvas.jsx](https://github.com/Anil-matcha/Open-AI-Design-Agent/blob/main/packages/design-agent/src/CreativeCanvas.jsx#L666-L737)：组织对话、附件与 canvas_state，再提交并读取任务事件。

对新项目的启发：可以研究素材 ID、源图 / 结果对照、计划展示和事件组织。

边界：公开主路径不足以验证或独立复现远端核心 planner、真实图像观察和修复策略，因此不把它作为完整开源 Agent runtime 参考。

许可：[仓库 MIT](https://github.com/Anil-matcha/Open-AI-Design-Agent/blob/main/LICENSE)，外部服务和图像模型的使用条件单独计算。

## 5. LangGraph：运行机制

项目：[langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)。[MIT 许可](https://github.com/langchain-ai/langgraph/blob/main/LICENSE)。

本项目选择它承载通用模型 / 工具循环、检查点和等待恢复。图像工具与项目状态由我们实现。

- [Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)：区分预定路径与动态工具使用。
- [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：区分执行检查点与跨任务数据；内存保存不具有进程重启后的持久性。
- [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：等待外部输入及恢复；副作用需要幂等保护或独立拆分。

使用框架不自动产生 Agent。新项目是否实现目标，以模型实际选择动作、读取真实结果并改变后续行为的任务轨迹为依据。

## 6. 对新架构的结论

主控决策与图像观察参考 T2I-Copilot / GenArtist；工具与素材交互参考 Jaaz 的公开机制；运行持久化参考 LangGraph。模型和工具组成包装专用能力，项目工作区与可靠任务执行独立实现。

具体架构与阶段验收见 [整体架构](./ARCHITECTURE.md)。
