# 架构映射说明

本文件用于把《软件整体交互逻辑与开发框架设计说明》中的架构要求映射到当前工程目录。

## 分层映射

- UI 表现层
  - `gpr_lab_pro/ui`
- 应用控制层
  - `gpr_lab_pro/app/context.py`
  - `gpr_lab_pro/app/controllers/`
  - `gpr_lab_pro/application.py`
- 流水线编排层
  - `gpr_lab_pro/processing/pipeline/`
  - `gpr_lab_pro/processing/runtime.py`
- 算法引擎层
  - `gpr_lab_pro/processing/engines/`
  - `gpr_lab_pro/processing/transforms/`
  - `gpr_lab_pro/processing/engine.py`
  - `gpr_lab_pro/algorithms/`
- 基础设施层
  - `gpr_lab_pro/infrastructure/`
  - `gpr_lab_pro/io/`

## 当前控制器职责

- `ProjectController`
  - 项目打开状态、模板索引等项目级信息。
- `DatasetController`
  - DAT 导入和数据集状态写入。
- `PipelineController`
  - 流程步骤管理、快照初始化、流程执行结果写回。
- `DisplayController`
  - 显示状态更新、B/C/A-scan 联动选择、渲染发布。
- `ExportController`
  - 流程模板保存/加载、处理结果导出。
- `TaskController`
  - 后台异步任务调度、忙碌状态回传、错误转发。

## 当前阶段说明

- MATLAB demo 的算法主体仍通过迁移后的共享处理器执行。
- 为了优先建立正式版工程框架，频域引擎、时域引擎、时频域桥接层先以清晰分层和注册入口为主。
- 后续迁移将逐步把算法从共享处理器拆分到对应层级中。
