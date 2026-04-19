# 打包与交付

## 推荐交付方式

推荐使用 `PyInstaller` 生成 `one-folder` 发行包。

这样生成的目录包含：

- `GPR_V11_Pyside.exe`
- Qt 运行库
- Python 依赖库

客户电脑不需要预装 Python。只要把整个目录打包成压缩包发给客户，客户解压后直接双击 `GPR_V11_Pyside.exe` 即可。

## 一键打包

在工程根目录执行：

```powershell
.\build_release.ps1
```

或者双击：

```bat
build_release.bat
```

打包完成后会生成：

- `release\GPR_V11_Pyside`
- `release\GPR_V11_Pyside.zip`

## 交付建议

发给客户时，优先发送：

- `release\GPR_V11_Pyside.zip`

客户使用方式：

1. 解压压缩包
2. 进入 `GPR_V11_Pyside` 文件夹
3. 双击 `GPR_V11_Pyside.exe`

## 注意

- 不建议用 `--onefile` 单文件模式。对于 PySide6 + matplotlib 的桌面软件，`one-folder` 更稳定，启动更快，也更容易排查问题。
- 如果后续增加图标、帮助文档、示例模板或授权文件，可以继续在 `GPR_V11_Pyside.spec` 里把这些资源打包进去。
