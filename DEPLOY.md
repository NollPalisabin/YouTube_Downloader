# 部署说明（Windows 桌面版）

本文对应「双击 exe 直接可用」的交付形态。产物已经构建好，按下面三步即可上线。

---

## 1. 产物位置

构建产物是一个**目录**（onedir 形态，不是单文件）：

```
dist\YouTubeDownloader\
├── YouTubeDownloader.exe      22.5 MB   主程序
└── _internal\                 419.3 MB  运行时（Python、Streamlit、yt-dlp、FFmpeg…）
    ├── app.py                           Streamlit 界面脚本（运行时加载）
    ├── assets\pythonnet.runtimeconfig.json
    └── bin\
        ├── ffmpeg.exe           105.5 MB  随包 FFmpeg
        └── ffprobe.exe          105.3 MB
```

整体约 **442 MB**。

> `YouTubeDownloader.exe` 与 `_internal` 必须放在**同一层**。只复制 exe 是跑不起来的
> （会弹窗提示"找不到 app.py"并写入日志）。

## 2. 放到 D:\YouTube_Downloader

```powershell
# 在仓库根目录执行
xcopy "dist\YouTubeDownloader" "D:\YouTube_Downloader" /E /I /Y
```

或者直接资源管理器里把 `dist\YouTubeDownloader` 整个文件夹复制过去，再改名为
`YouTube_Downloader`。放好后长这样：

```
D:\YouTube_Downloader\
├── YouTubeDownloader.exe
├── _internal\
├── downloads\        ← 首次启动自动创建，默认下载目录
├── logs\app.log      ← 启动日志（排障用）
└── settings.json     ← 下载目录覆盖、快捷方式标记
```

## 3. 首次运行

1. 双击 `D:\YouTube_Downloader\YouTubeDownloader.exe`；
2. 程序会**自动在桌面创建快捷方式**「YouTube 下载器」（已存在则跳过）；
3. 界面自动打开（见第 4 节的两种模式）；
4. 之后直接双击桌面快捷方式即可。

快捷方式的属性（已实测）：

| 属性 | 值 |
| --- | --- |
| TargetPath | `D:\YouTube_Downloader\YouTubeDownloader.exe` |
| WorkingDirectory | `D:\YouTube_Downloader` |
| IconLocation | `D:\YouTube_Downloader\YouTubeDownloader.exe,0` |

如果桌面没有出现快捷方式，三种补建方式任选：

- 程序里左侧栏 →「创建桌面快捷方式」；
- 命令行：`YouTubeDownloader.exe --create-shortcut`；
- 开发机装过 Python 时：`python setup_shortcut.py --exe D:\YouTube_Downloader\YouTubeDownloader.exe --force`。

## 4. 界面打开方式（重要）

程序**优先**尝试用 pywebview 打开真正的桌面窗口；**当前环境会自动回退到浏览器模式**
（原因见第 7 节），表现为：

1. 默认浏览器自动打开 `http://localhost:<随机端口>`，无需手动输入地址；
2. 同时出现一个小窗口「YouTube 视频下载器 — 运行中」，上面有「再次打开浏览器」和「退出程序」；
3. **关闭这个小窗口 = 退出程序**，Streamlit 服务随之结束（实测关闭后无残留进程）。
   浏览器标签页可以继续留着，但服务停止后刷新会失效。

强制指定模式：

```powershell
YouTubeDownloader.exe --browser    # 强制浏览器模式
YouTubeDownloader.exe --webview    # 强制桌面窗口模式（环境不支持时会报错退出）
```

**窗口模式失败会被记住**：首次尝试失败后程序把 `"webview_unavailable": true` 写进
`settings.json`，之后每次启动直接进浏览器模式，不再空等（实测首启 4.7 秒 → 后续 2.1 秒）。

想重新尝试窗口模式（例如你升级了 pywebview 或安装了 .NET 运行时）：

```powershell
YouTubeDownloader.exe --webview        # 手动重试一次；成功后会自动清除该标记
```

或直接删掉 `settings.json` 里的 `webview_unavailable` 这一行。

## 5. FFmpeg 捆绑在哪里

- 随包位置：`D:\YouTube_Downloader\_internal\bin\ffmpeg.exe` 与 `ffprobe.exe`；
- 程序内检测顺序（`config.py`）：
  1. 环境变量 `YTDL_FFMPEG_PATH` / `FFMPEG_PATH`
  2. **随包捆绑**：`_internal\bin\`，其次 `exe 同级\bin\`
  3. 系统 PATH
  4. 常见安装目录（winget / scoop / choco / 注册表 PATH）
- 验证方式：`YouTubeDownloader.exe --self-check` 会在 `logs\app.log` 里输出
  `ffmpeg_source: bundled:...\bin` 与版本号。

目标机器不需要预装 FFmpeg；即使装了系统版，也优先用随包的那一份。

## 6. 下载目录

| 优先级 | 来源 |
| --- | --- |
| 1 | 环境变量 `YTDL_DOWNLOAD_DIR` |
| 2 | `settings.json` 里的 `download_dir`（界面左侧「修改下载目录」写入） |
| 3 | 部署目录下的 `downloads\`（即 `D:\YouTube_Downloader\downloads`） |
| 4 | 若上面不可写：`%USERPROFILE%\Downloads\YouTube_Downloader` |

`settings.json` 示例：

```json
{
  "download_dir": "D:\\Videos\\YouTube",
  "shortcut_created": true
}
```

## 7. 已知限制与原因

**桌面窗口模式在本机不可用，会自动回退浏览器模式。** 实测证据：

| 尝试 | 结果 |
| --- | --- |
| pythonnet 默认（.NET Framework / netfx） | `RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize` —— pythonnet 3.x 的 Python.Runtime.dll 只能跑 CoreCLR |
| 强制 `PYTHONNET_RUNTIME=coreclr` + 预加载 WinForms | pywebview 5.4 的后端引用了 `System.Windows.Forms.ContextMenu`，该类型在 .NET Core 中已被移除 → `TypeLoadException` |
| coreclr + WindowsDesktop runtimeconfig（已内置） | WinForms 可加载，但创建 `WebView2.WinForms.WebView2` 类型失败 |

即 **pywebview 5.4 与 pythonnet 3.x 的组合不兼容**（这是 `streamlit-desktop-app` 的依赖约束）。
按方案约定，退回「PyInstaller + 自动打开默认浏览器 + 宿主窗口」的实现，
并保留窗口模式代码：以后 pywebview 修复后，程序会自动重新用上窗口模式，无需改配置。

其他限制：

- 目标机需要 **Edge WebView2 Runtime**（Windows 11 自带）与 **.NET Framework 4.8**
  才能尝试窗口模式；缺了不影响浏览器模式；
- 首次运行可能出现 SmartScreen「Windows 已保护你的电脑」→ 点「更多信息」→「仍要运行」；
- 浏览器模式下关闭浏览器标签页不会退出程序，请关掉宿主小窗。

## 8. 日志与排障

- 日志：`D:\YouTube_Downloader\logs\app.log`（UTF-8，含启动参数、FFmpeg 来源、错误堆栈）；
- 常见问题：
  - 「找不到 app.py」→ `_internal` 没跟 exe 放在一起；
  - 「桌面窗口不可用…回退浏览器模式」→ 正常现象，见第 7 节；
  - 下载报 FFmpeg 相关错误 → 检查 `_internal\bin\ffmpeg.exe` 是否存在，或点界面「重新检测」；
  - 反爬提示「要求人机验证」→ 导出浏览器 cookies 为 Netscape 格式，设环境变量
    `YTDL_COOKIES_FILE` 指向它后重启。

## 9. 重新构建

```bat
python -m pip install -r requirements-build.txt
build.bat            :: 窗口版 exe（默认）
build.bat console    :: 带控制台的版本，排障用
```

构建脚本会自动：生成图标 → 检查 `vendor\bin` 里的 FFmpeg → 清理旧产物 → 调 PyInstaller
→ 校验 `exe / _internal\app.py / _internal\bin\ffmpeg(.exe|probe.exe)` 是否齐全。

FFmpeg 若不存在，从 <https://www.gyan.dev/ffmpeg/builds/> 下载 `essentials` 包，
把 `ffmpeg.exe` 与 `ffprobe.exe` 放进 `vendor\bin\`。

## 11. 分发给其他人

### Windows：把整个目录压包发过去

```powershell
Compress-Archive -Path "D:\YouTube_Downloader" -DestinationPath "YouTubeDownloader-windows.zip"
```

对方拿到后：解压到任意位置 → 双击 `YouTubeDownloader.exe` → 首次运行会在那个位置建
`downloads\`、`logs\` 和桌面快捷方式（快捷方式指向**他解压出来的真实路径**，与你的 `D:\`
无关）。对方机器不需要装 Python 或 FFmpeg。

注意事项：

- **必须发整个目录**，只发 exe 会因为缺 `_internal` 而报错；
- 首次运行可能遇到 SmartScreen →「更多信息」→「仍要运行」；
- 要消除这个提示需要代码签名证书（Windows Authenticode）。

### macOS：必须单独构建（不能复用 Windows 产物）

PyInstaller 不能交叉编译。两条路：

1. **一台 Mac**（推荐有 Apple Silicon 的机器）：

   ```bash
   python3 -m pip install -r requirements-build.txt
   python3 -m pip install imageio-ffmpeg
   mkdir -p vendor/bin-macos
   cp "$(python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" vendor/bin-macos/ffmpeg
   bash build_macos.sh          # → dist/YouTubeDownloader.app
   ```

2. **没有 Mac → GitHub Actions**：仓库已带 `.github/workflows/build.yml`，
   在 Actions 里手动 Run workflow（或打 `v*` tag），`macos-14` job 会产出
   `YouTubeDownloader-macos.zip`（内含 `.app`），Windows job 同时产出 onedir 压缩包。

macOS 产物分发给别人时：

| 事项 | 说明 |
| --- | --- |
| 架构 | `macos-14` runner 是 arm64；Intel Mac 用 `macos-13` 的 `macos-app-intel` 产物（arm64 包**不能**在 Intel 上跑） |
| Gatekeeper（Sequoia 15 / Tahoe 26） | 双击后提示「Apple 无法验证」→ **系统设置 → 隐私与安全性** → 底部点「仍要打开」→ 输密码。此版本已取消"右键→打开"的绕过方式 |
| Gatekeeper（Sonoma 14 及更早） | 在 App 上右键 →「打开」→ 再点「打开」 |
| 提示「已损坏」 | `xattr -dr com.apple.quarantine /Applications/YouTubeDownloader.app` 后重开 |
| 签名 | `build_macos.sh` 会做 **ad-hoc 签名**（`codesign --sign -`），避免 Apple Silicon 上因无签名导致"已损坏/被杀"；但它**不是**开发者证书，Gatekeeper 仍会提示。要彻底免提示需 Apple Developer ID（约 $99/年）+ `notarytool` 公证 |
| 安装方式 | 把 `YouTubeDownloader.app` 拖进「应用程序」；首次启动后桌面会出现指向它的符号链接 |
| 配置与日志 | `~/Library/Application Support/YouTubeDownloader/`（`logs/app.log`） |
| 下载目录 | 默认 `~/Downloads/YouTubeDownloader`，界面里可改 |

> 状态：macOS / Linux 的代码分支已有单测覆盖，但 `.app` 的真机构建与运行**尚未验证**——
> 需要跑一次 CI 或在 Mac 上执行 `build_macos.sh` 后确认。

## 12. 遇到「要求人机验证」怎么办

YouTube 对部分 IP / 无登录态的请求会返回 `Sign in to confirm you're not a bot`。
这不是程序故障，在**应用左侧栏「访问凭据」**里配置 cookies 即可，二选一：

| 方式 | 操作 | 说明 |
| --- | --- | --- |
| 从浏览器读取（最省事） | 下拉选 Safari / Chrome / Edge / Firefox | 浏览器需**已登录 YouTube**。Chrome/Edge 读取前要完全退出浏览器；Safari 会请求「钥匙串」授权，macOS 还可能需要给应用「完全磁盘访问权限」 |
| 上传 cookies.txt（最稳） | 用浏览器插件（如 Get cookies.txt LOCALLY）导出后上传 | 保存到 `~/Library/Application Support/YouTubeDownloader/cookies.txt`（Windows 为 exe 同级），界面里可随时「清除」 |

配置完重新点「解析」即可。优先级：环境变量 `YTDL_COOKIES_FILE` > 界面导入的文件 > 从浏览器读取。

> cookies 等同于你的登录凭据，**不要发给别人**；`.gitignore` 已排除 `cookies.txt`。

## 13. 卸载

1. 删除桌面快捷方式 `YouTube 下载器.lnk`；
2. 删除目录 `D:\YouTube_Downloader`（内含下载文件与 `settings.json`，如要保留视频请先移出 `downloads\`）。
