# YouTube 视频下载器

一个干净、现代、响应式的 Streamlit 应用：粘贴 YouTube 链接 → 解析清晰度 → 下载并自动用 FFmpeg 合并为 MP4。

- 前端：Streamlit
- 下载核心：yt-dlp（封装调用，不自研下载逻辑）
- 合并：FFmpeg（自动检测系统安装）

> 当前进度：**全部功能已完成，并已打包为 Windows 桌面交付版**（exe + 捆绑 FFmpeg + 桌面快捷方式）。
> 打包与部署细节见 **[DEPLOY.md](DEPLOY.md)**。

---

## 一、目录结构

```
youtube-downloader/
├── app.py                  # 界面入口：状态编排（解析 / 下载 / 重置）
├── desktop_entry.py        # 桌面入口：打包后启动点（窗口/浏览器模式、日志、首启快捷方式）
├── config.py               # 下载路径 + FFmpeg 自动检测 + 格式选择串
├── paths.py                # 冻结 + 跨平台路径层（资源 / 数据 / 下载目录）
├── desktop.py              # 桌面集成门面（按平台选实现）
├── desktop_win.py          # Windows：PowerShell 生成 .lnk
├── desktop_posix.py        # macOS：.app 符号链接；Linux：.desktop
├── setup_shortcut.py       # 命令行生成桌面快捷方式（可选）
├── make_icon.py            # 生成 assets/app.ico 与 app.iconset（macOS 用）
├── build.bat               # Windows 一键打包（onedir）
├── build_macos.sh          # macOS 打包脚本（产出 .app）
├── .github/workflows/build.yml  # CI：Windows + macOS 双平台出包
├── DEPLOY.md               # 部署与分发说明（Windows / macOS）
├── requirements.txt        # 运行依赖
├── requirements-dev.txt    # 测试依赖
├── requirements-build.txt  # 打包依赖（pyinstaller + streamlit-desktop-app）
├── pytest.ini              # 测试配置
├── .streamlit/config.toml  # 开发模式的 Streamlit 配置（打包后改为显式传参）
├── .gitignore
├── README.md
├── core/                   # 业务逻辑层（不依赖 Streamlit）
│   ├── __init__.py
│   ├── utils.py            # 时长、体积、速度格式化，文件名清洗
│   ├── extractor.py        # 链接解析：标题/封面/时长/清晰度列表
│   └── downloader.py       # 下载 + 进度回调 + FFmpeg 合并 + 取消
├── ui/                     # 表现层
│   ├── __init__.py
│   ├── styles.py           # 自定义 CSS（浅色卡片风、响应式）
│   └── components.py       # Hero / 侧栏 / 信息卡 / 清晰度 / 进度 / 结果
├── assets/                 # 图标（.ico / .iconset）+ pythonnet runtimeconfig
├── vendor/bin/             # 构建用 Windows FFmpeg（打入 _internal\bin）
├── vendor/bin-macos/       # 构建用 macOS FFmpeg（打入 .app）
├── downloads/              # 开发模式默认下载目录
└── tests/
    ├── conftest.py         # 临时目录夹具 + 用户状态隔离
    ├── test_config.py      # 配置、FFmpeg 检测、下载目录优先级
    ├── test_paths.py       # 开发/打包/三平台 的路径解析
    ├── test_desktop.py     # Windows 快捷方式（.lnk）创建与首启逻辑
    ├── test_desktop_posix.py  # macOS 符号链接 / Linux .desktop
    ├── test_desktop_entry.py  # 窗口模式失败记忆逻辑
    ├── test_utils.py       # 格式化边界值
    ├── test_extractor.py   # 解析、排序、错误翻译、缓存
    ├── test_downloader.py  # 进度合成、路径解析、取消、异常
    └── test_app.py         # AppTest 驱动界面：解析/下载/取消/路径展示/改目录
```

## 二、环境要求

| 依赖 | 版本 / 说明 |
| --- | --- |
| Python | 3.11+（已在 3.12.10 验证）；只有源码运行/打包需要 |
| FFmpeg | 源码运行需自行安装；**打包版已随 exe 捆绑**（`_internal\bin`），目标机无需安装 |
| 网络 | 需要能访问 YouTube |

## 三、安装与运行

```bash
# 1. 创建虚拟环境（可选但推荐）
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. 安装依赖
python -m pip install -r requirements.txt

# 3. 环境自检（确认 FFmpeg 是否被检测到）
python config.py

# 4. 启动
streamlit run app.py
```

### 安装 FFmpeg

- **Windows**：`winget install --id Gyan.FFmpeg -e`，或 `choco install ffmpeg -y`，或 `scoop install ffmpeg`；
  手动安装可下载 <https://www.gyan.dev/ffmpeg/builds/>，把 `bin` 目录加入 PATH。
- **macOS**：`brew install ffmpeg`
- **Linux**：`sudo apt install ffmpeg`

若 FFmpeg 已安装但不在 PATH，可设置环境变量 `YTDL_FFMPEG_PATH` 指向 `ffmpeg.exe` 或其 `bin` 目录，
应用会优先使用它；界面上也提供「重新检测」按钮。

## 四、配置项（config.py）

| 名称 | 作用 | 是否可由环境变量覆盖 |
| --- | --- | --- |
| `get_download_dir()` | 默认下载目录，缺省为 `./downloads` | 是：`YTDL_DOWNLOAD_DIR` |
| `ensure_download_dir()` | 确保目录存在，失败时给出可读提示 | — |
| `detect_ffmpeg()` | 检测 FFmpeg/FFprobe（环境变量 → PATH → winget portable 包 / 注册表最新 PATH / 常见安装目录），带缓存与 `refresh` | 是：`YTDL_FFMPEG_PATH`、`FFMPEG_PATH` |
| `require_ffmpeg()` | 未安装时抛 `FFmpegNotFoundError`（含安装指引），而不是崩溃 | — |
| `ffmpeg_install_hint()` | 按平台返回安装指引文本 | — |
| `build_format_selector(h)` | 生成 yt-dlp 格式选择串（视频流 + 音频流，自动兜底） | — |
| `is_supported_url()` | 链接预校验，过滤明显非法输入 | — |
| `get_cookies_file()` | 可选 cookies 文件，用于年龄限制 / 人机验证 | 是：`YTDL_COOKIES_FILE` |
| `environment_report()` | 汇总 Python / 目录 / FFmpeg 状态，供界面顶部状态条使用 | — |

## 五、清晰度与格式策略（core/extractor.py）

解析结果按**视频高度从高到低**排列，同一高度只保留一项（展示该档的代表流）：

1. **编码**：优先 H.264（`avc1`），兼容性最好；该档没有 H.264 时退回 MP4 容器里的 AV1 / VP9；
2. **体积估算**：取"视频流体积 + 最佳音轨体积"，与实际合并产物接近；拿不到体积时显示"未知"；
3. **代表流的选择**参照 yt-dlp 的真实挑选逻辑（优先有体积信息的 https/DASH 流，而非 HLS 变体），
   避免"界面显示的编码/体积"和"实际下载的流"不一致；
4. **末尾额外一项**「仅音频 · MP3」：下载最佳音轨后用 FFmpeg 转 192kbps MP3；
5. 自动过滤 storyboard 预览图、DRM 流；直播与 DRM 内容直接给出明确提示。

对应格式选择串（见 `config.build_format_selector`）逐级降级：

```
bv*[height<=H][vcodec^=avc1]+ba[ext=m4a]   # H.264 视频 + AAC 音频
→ bv*[height<=H][ext=mp4]+ba[ext=m4a]      # 任意 MP4 视频 + AAC
→ bv*[height<=H]+ba[ext=m4a]               # 任意容器视频 + AAC
→ bv*[height<=H]+ba/b[height<=H]           # 任意音视频流
→ bv*+ba/b                                 # 放弃高度限制，保证有结果
```

解析结果在进程内缓存最近 8 条，界面上的「重新解析」使用 `refresh=True` 绕过缓存。

## 六、下载与进度（core/downloader.py）

`DownloadJob` 是下载单元，`execute()` 阻塞执行，`start()` 放入后台线程，界面主线程用
`snapshot()` 轮询只读快照 `DownloadProgress`。

| 关注点 | 实现方式 |
| --- | --- |
| 下载核心 | `yt_dlp.YoutubeDL` + `format` 选择串，视频流与音频流分别下载 |
| 合并 | `ffmpeg_location` 指向 `config.require_ffmpeg().directory` + `merge_output_format="mp4"` |
| 仅音频 | 追加 `FFmpegExtractAudio` 后处理器，转 192kbps MP3 |
| 进度 | 两路流各占一半权重合成总进度（视频 0→50%，音频 50→99%，合并阶段停在 95%+），完成置 100% |
| 速度 / 剩余时间 | 直接取 yt-dlp 的 `speed` / `eta`，界面用 `format_speed` / `format_eta` 渲染 |
| 文件名 | `标题 [视频ID][1080p].mp4`、`标题 [视频ID][audio].mp3`，不同清晰度互不覆盖 |
| 结果路径 | 优先取 yt-dlp 返回的 `filepath`，拿不到时按视频 ID 在下载目录里找最新的非 `.part` 成品 |
| 取消 | `cancel()` 置位，进度回调里抛 `DownloadCancelled` 中断，并清理本次的 `.part` 分片 |
| 失败 | 复用 `classify_error()` 翻译成中文；FFmpeg 缺失转为 `DownloadFailed(hint=安装指引)` |

状态机：`pending → downloading → merging → finished`，异常分支为 `failed` / `cancelled`。
失败时**保留** `.part` 文件，便于下次断点续传；取消时**删除**，避免留下垃圾。

## 七、界面与组件（app.py / ui/）

界面是"问答式"单页：**Hero 区 → 链接输入 → 视频信息卡 → 清晰度列表 → 进度 → 完整路径**。

| 模块 | 内容 |
| --- | --- |
| `ui/styles.py` | 约 60 行 CSS：浅色卡片（圆角 16px、细描边、克制阴影）、红色主色、清晰度单选项选中态、进度条渐变、路径代码块等宽字体、窄屏（≤820px）折行适配 |
| `ui/components.py` | `page_header` / `sidebar_panel` / `video_card` / `quality_selector` / `download_row` / `progress_view`，只渲染和在需要时返回 `"reset"` 动作 |
| `app.py` | 只做状态编排：解析结果与 `DownloadJob` 存 `st.session_state`，下载在后台线程跑 |

交互细节：

- 视频信息卡展示封面、标题、UP 主、时长、上传日期、播放量，并附「在 YouTube 打开」；
- 清晰度是单选列表，每项下方用小字标注容器 / 编码 / 预计体积（含「仅音频 · MP3」）；
- 下载中：进度条 + 百分比 + 阶段文案 + 已下载 / 速度 / 剩余三块指标，并可用「取消下载」中断；
  该区域用 `st.fragment(run_every=0.4)` 局部刷新，不整页重绘；
- 完成后：`st.success` + 等宽字体展示**文件完整路径** + 大小 / 时长 / 清晰度；
- 左侧栏常驻环境状态（FFmpeg 版本与路径、下载目录、重新检测按钮、使用步骤与合规提示）。

## 八、实现路线

| 步骤 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 目录结构、`requirements.txt`、`config.py`（含 FFmpeg 检测） | ✅ 已完成 |
| 2 | `core/utils.py` + `core/extractor.py`：解析标题/封面/时长/清晰度列表 | ✅ 已完成 |
| 3 | `core/downloader.py`：yt-dlp 下载、进度回调、FFmpeg 合并、完成路径返回 | ✅ 已完成 |
| 4 | `app.py`：链接输入、信息展示、清晰度选择、下载触发与进度条 | ✅ 已完成 |
| 5 | `ui/styles.py` + `ui/components.py`：现代响应式样式与错误提示打磨 | ✅ 已完成 |
| 6 | 测试与真机验证（含 FFmpeg 缺失、无效链接、网络失败场景） | ✅ 已完成 |

## 九、错误处理约定

| 场景 | 处理方式 |
| --- | --- |
| 链接非法 / 非 YouTube | 输入预校验，界面内联提示，不调用 yt-dlp |
| 视频不可用 / 私有 / 地区限制 | 捕获 yt-dlp 异常，转成中文可读提示 |
| 网络错误 / 超时 | 沿用 `RETRIES` 重试，失败后提示检查网络与代理 |
| FFmpeg 未安装 | `require_ffmpeg()` 抛 `FFmpegNotFoundError`，界面展示安装指引，应用不崩溃 |
| 磁盘不可写 | `ensure_download_dir()` 提示改用 `YTDL_DOWNLOAD_DIR` |

## 十、桌面化交付（exe）

详细部署步骤见 **[DEPLOY.md](DEPLOY.md)**，这里是设计要点。

### 产物形态（已实测）

```
D:\YouTube_Downloader\
├── YouTubeDownloader.exe    22.5 MB
├── _internal\              419.3 MB   Python/Streamlit/yt-dlp/FFmpeg
│   ├── app.py                         运行时加载的界面脚本
│   ├── assets\pythonnet.runtimeconfig.json
│   └── bin\ffmpeg.exe + ffprobe.exe   随包 FFmpeg（105.5 / 105.3 MB）
├── downloads\              首启自动创建
├── logs\app.log
└── settings.json
```

整体 **441.9 MB**，`onedir` 形态（非单文件：单文件每次启动要解压整包，且 `_MEIPASS` 每次都变）。

### 路径层（`paths.py`）

| 用途 | 开发模式 | 打包模式 |
| --- | --- | --- |
| 只读资源 `resource_root()` | 项目根 | `sys._MEIPASS`（onedir 即 `_internal`） |
| 可写数据 `app_dir()` | 项目根 | exe 所在目录 |
| FFmpeg 候选 `bundled_bin_dirs()` | `<root>\bin` | `_internal\bin` → `<exe>\bin` |

FFmpeg 检测顺序变为：环境变量 → **捆绑目录** → 系统 PATH → 常见安装目录。
下载目录优先级：环境变量 → `settings.json` → 部署目录 `downloads\` → 不可写时回退
`%USERPROFILE%\Downloads\YouTube_Downloader`（界面左侧可改，写入 `settings.json`）。

### 快捷方式（`desktop.py`）

桌面路径用 `ctypes` 调 `SHGetKnownFolderPath(FOLDERID_Desktop)`（正确处理 OneDrive 重定向），
`.lnk` 用 Windows 自带的 PowerShell `WScript.Shell` 创建，**不引入 pywin32**。
首启静默创建 + 侧栏按钮 + `setup_shortcut.py` 三个入口，幂等（已存在则跳过）。

### 桌面窗口层的结论（重要）

原计划用 `streamlit-desktop-app`（pywebview）开原生窗口，实测在本环境**不可用**：

| 尝试 | 结果 |
| --- | --- |
| pythonnet 默认 netfx | `Failed to resolve Python.Runtime.Loader.Initialize` |
| 强制 coreclr | pywebview 引用 `System.Windows.Forms.ContextMenu`（.NET Core 已移除）→ `TypeLoadException` |
| coreclr + WindowsDesktop runtimeconfig | WinForms 可加载，但 `WebView2.WinForms.WebView2` 类型创建失败 |

按方案约定回退为「PyInstaller + 自动打开默认浏览器 + 宿主窗口」：`desktop_entry.py` 先尝试窗口模式，
失败则**自动**起服务、打开浏览器、显示宿主小窗（关闭即退出，实测无残留进程）。
失败结果记在 `settings.json` 的 `webview_unavailable`，之后启动直接进浏览器模式
（首启 4.7 秒 → 后续 2.1 秒）；`--webview` 可强制重试，成功会自动清除该标记。
窗口模式代码保留，环境修复后无需改配置即可重新启用。

### 打包

```bat
python -m pip install -r requirements-build.txt
build.bat            :: 窗口版（--windowed）
build.bat console    :: 带控制台（排障）
```

`build.bat` 会校验 `exe`、`_internal\app.py`、`_internal\bin\ffmpeg(.exe|probe.exe)` 是否齐全；
PyInstaller 参数含义见脚本内注释（`--collect-all streamlit` + `--copy-metadata streamlit`、
`--collect-all yt_dlp`、`--collect-all webview/clr_loader/pythonnet`、`--add-data` 三处资源）。

### 打包后的自检开关

```powershell
YouTubeDownloader.exe --self-check    # 环境 + FFmpeg 来源（写入 logs\app.log）
YouTubeDownloader.exe --smoke-test    # 解析 + 下载最小清晰度 + FFmpeg 合并
```

## 十一、跨平台与分发（macOS / Linux）

### 平台矩阵

| 项目 | Windows | macOS | Linux |
| --- | --- | --- | --- |
| 可执行形态 | onedir（exe + `_internal`） | `YouTubeDownloader.app` | onedir（无 .app） |
| 只读资源 | `_internal\` | `Contents/Frameworks/` | `_internal/` |
| 可写数据（settings/logs） | exe 同级 | `~/Library/Application Support/YouTubeDownloader` | `$XDG_DATA_HOME/YouTubeDownloader`（默认 `~/.local/share/…`） |
| 默认下载目录 | `<exe>\downloads` | `~/Downloads/YouTubeDownloader` | `~/Downloads/YouTubeDownloader` |
| 桌面快捷方式 | PowerShell 生成 `.lnk` | 桌面放指向 `.app` 的符号链接 | 桌面 `.desktop` 启动器 |
| 构建脚本 | `build.bat` | `build_macos.sh` | 同 macOS（去掉 `--windowed` 的 bundle 参数） |
| FFmpeg 来源 | gyan.dev essentials | 静态 ffmpeg（imageio-ffmpeg 或 evermeet） | 同 macOS |

平台差异集中在三个文件：`paths.py`（目录约定）、`desktop.py`（门面）+ `desktop_win.py` /
`desktop_posix.py`（各平台快捷方式）、`desktop_entry.py`（`_prepare_dotnet` 仅 Windows 生效）。

### macOS 构建（必须在 Mac 上，或走 CI）

PyInstaller **不能交叉编译**，Windows 上产不出 macOS 二进制。

```bash
# 在 macOS 上（arm64 或 x86_64，与目标机器一致）
python3 -m pip install -r requirements-build.txt
mkdir -p vendor/bin-macos
python3 -m pip install imageio-ffmpeg
cp "$(python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" vendor/bin-macos/ffmpeg
bash build_macos.sh            # → dist/YouTubeDownloader.app
```

三个必须知道的点：

1. **架构不通用**：Apple Silicon(arm64) 与 Intel(x86_64) 的产物不能互换；`universal2` 需要
   universal2 的 Python + universal2 的 ffmpeg，实践上按需各构建一次（CI 里两个 runner 都配了）。
2. **签名**：脚本会做 **ad-hoc 签名**（`codesign --force --deep --sign -`），这是 Apple Silicon
   上运行的最低要求，能避免「已损坏 / 被系统杀掉」；但它不是开发者证书，Gatekeeper 仍会提示：
   - macOS 15 Sequoia 及更新：系统设置 → 隐私与安全性 → 「仍要打开」
   - macOS 14 及更早：右键 →「打开」
   要彻底免提示需 Apple Developer ID（约 $99/年）+ 公证（`notarytool`）。
3. **窗口模式**：macOS 走 PyObjC + WKWebView，理论上能开出真正的桌面窗口（Windows 撞到的是
   .NET/pythonnet 问题）；若失败同样自动回退浏览器模式，逻辑与 Windows 一致。

### CI 双平台出包

`.github/workflows/build.yml` 定义了 `windows-latest` 与 `macos-14` 两个 job：
各自安装依赖、拉取对应平台的 FFmpeg、跑测试、构建，产物上传为 artifact。
手动触发（Actions → Run workflow）或打 `v*` tag 即可。

> 状态说明：Windows 产物已在本机完整验证；**macOS / Linux 产物尚未真机验证**——
> 代码层的平台分支有单测覆盖（`tests/test_paths.py`、`tests/test_desktop_posix.py`），
> 但 `.app` 的实际构建与运行需要在 macOS 上跑一次 CI 才能确认。

## 十二、测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

当前共 162 项测试，覆盖：

- 配置与 FFmpeg 检测：winget portable 布局、注册表 PATH、损坏二进制、缺 ffprobe 降级、环境变量指向目录/文件、**捆绑层优先于系统 PATH**、打包模式安装提示；
- 路径层：开发/打包（onedir 与 macOS .app）两种形态、**三平台目录约定**（Windows exe 同级 / macOS Application Support / Linux XDG）、settings 读写与原子写入、可写性探测；
- 桌面集成：Windows `.lnk` 的 TargetPath/WorkingDirectory、幂等与 force 覆盖、首启标记；macOS `.app` 符号链接目标推导、Linux `.desktop` 内容；
- 窗口模式记忆：失败后写入标记、`--webview` 强制重试、成功后清除标记、配置损坏时的兜底；
- 下载目录：环境变量 → settings → 部署目录 → 用户 Downloads 的优先级与不可写回退、界面改目录落盘；
- 桌面集成：快捷方式 TargetPath/WorkingDirectory、幂等与 force 覆盖、首启静默创建与标记；
- 格式化边界值（时长 / 体积 / 速度 / 播放量 / 文件名清洗）；
- 解析层：清晰度排序、体积估算（视频流 + 音轨）、HLS 变体与 DRM/storyboard 过滤、错误翻译表、缓存命中与容量；
- 下载层：两路流进度合成、单流识别、MP3 后处理器、输出路径回退、取消清理、后台线程异常记录、目录不可写；
- 界面层（`AppTest`）：初始渲染、非法链接提示、解析失败提示、信息卡与清晰度列表、下载完成展示完整路径、取消提示、下载中进度与取消按钮、修改下载目录。

真机验证记录：

1. **真实下载**：Big Buck Bunny 的 144p MP4（14.2 MB）与仅音频 MP3（14.5 MB），`ffprobe` 确认 MP4 内含 H.264 + AAC、时长与源一致；
2. **真实端到端**：`AppTest` 驱动真实界面代码 —— 解析真实链接 → 选 144p → 下载 → 页面展示完整路径；
3. **打包后（exe）**：`--self-check` 显示 `ffmpeg_source: bundled:...\_internal\bin`；
   `--smoke-test` 在 exe 内完成解析 → 144p 下载 → 合并，产物 14.2 MB，捆绑 ffprobe 校验含 h264 144p 视频流 + aac 音频流；
4. **部署验收**：exe 放到 `D:\YouTube_Downloader` 启动后服务健康检查 200、桌面快捷方式自动生成
   （TargetPath/WorkingDirectory 正确）、退出后 0 残留进程。
