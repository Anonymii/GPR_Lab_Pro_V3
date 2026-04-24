# GPR V11 PySide

`GPR V11 PySide` 是面向正式产品化交付的探地雷达后处理软件工程骨架，当前阶段优先完成整体架构搭建，并在此基础上逐步迁移 MATLAB demo 的算法与模板能力。

## 当前架构

本工程按《软件整体交互逻辑与开发框架设计说明》组织为以下层次：

- `gpr_lab_pro/ui`
  - PySide6 界面层，负责主窗口、菜单、参数输入与多视图显示。
- `gpr_lab_pro/app`
  - 应用控制层，包含 `ApplicationContext` 与各类控制器。
  - 当前已建立 `ProjectController`、`DatasetController`、`PipelineController`、`DisplayController`、`ExportController`、`TaskController`。
- `gpr_lab_pro/processing/pipeline`
  - 流水线编排层，负责流程执行、快照缓存、任务调度。
- `gpr_lab_pro/processing/engines`
  - 算法引擎分层骨架，按频域处理和时域处理分别组织。
- `gpr_lab_pro/processing/transforms`
  - 时频域桥接层骨架，用于后续承接 CZT / ISDFT / IFFT 的正式迁移。
- `gpr_lab_pro/render/adapters`
  - 渲染适配层，根据结果快照构建 B-scan、C-scan、A-scan、频谱显示数据。
- `gpr_lab_pro/domain`
  - 领域模型与状态对象。
- `gpr_lab_pro/infrastructure`
  - 设置、线程工作器、日志等基础设施。
- `gpr_lab_pro/io`
  - DAT 导入、项目保存、模板与数据持久化相关能力。

## 当前阶段目标

- 先保证正式版项目骨架、分层边界和数据流入口建立完成。
- 再分批迁移 MATLAB demo 算法到正式架构中。
- 深度功能验证、参数对齐和排错放在后续阶段逐项推进。

## 启动

```bash
python -m gpr_lab_pro.app
```

## Windows 运行步骤

1. 双击 `install_deps.bat`
2. 依赖安装完成后，双击 `run_app.bat`

也可以在 PowerShell 中执行：

```powershell
.\install_deps.bat
.\run_app.ps1
```

## 打包交付

如果需要把软件发给未安装 Python 的客户，建议使用 `PyInstaller` 打成 `one-folder` 交付包。

本工程已提供：

- [build_release.bat](/E:/code_management/GPR_V12_Pyside/GPR_Lab_Pro_V3/build_release.bat)
- [build_release.ps1](/E:/code_management/GPR_V12_Pyside/GPR_Lab_Pro_V3/build_release.ps1)
- [GPR_V11_Pyside.spec](/E:/code_management/GPR_V12_Pyside/GPR_Lab_Pro_V3/GPR_V11_Pyside.spec)
- [PACKAGING.md](/E:/code_management/GPR_V12_Pyside/GPR_Lab_Pro_V3/PACKAGING.md)

执行：

```powershell
.\build_release.ps1
```

打包完成后会生成：

- `release\GPR_Lab_Pro_V4`
- `release\GPR_Lab_Pro_V4.zip`

客户电脑无需安装 Python，但请务必先完整解压整个压缩包，再进入 `GPR_Lab_Pro_V4` 文件夹运行：

- `GPR_Lab_Pro_V4.exe`
或
- `release_launcher.bat`

如果启动失败，程序会在运行目录下生成 `startup_error.log`，可将该文件发回用于定位问题。
