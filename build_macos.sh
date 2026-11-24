#!/usr/bin/env bash
# ============================================================================
#  YouTube Downloader —— macOS 构建脚本（必须在 macOS 上运行）
#
#  产物：dist/YouTubeDownloader.app（双击即用的应用包）
#
#  依赖：Python 3.11/3.12（与目标架构一致）、requirements-build.txt
#  FFmpeg：需要 vendor/bin-macos/ffmpeg 存在（可用 imageio-ffmpeg 取，见 CI）
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

APPNAME="YouTubeDownloader"
PYTHON="${PYTHON:-python3}"

echo "[1/8] 检查平台与 Python"
if [ "$(uname -s)" != "Darwin" ]; then
  echo "[ERROR] 此脚本只能在 macOS 上运行（PyInstaller 不支持交叉编译）" >&2
  exit 10
fi
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' || {
  echo "[ERROR] 需要 Python 3.11+" >&2
  exit 11
}
echo "      $(uname -m) / $("$PYTHON" -V)"

echo "[2/8] 检查构建依赖"
"$PYTHON" -c 'import PyInstaller, streamlit, yt_dlp; print("      deps ok")' || {
  echo "[ERROR] 缺少依赖，请先执行: $PYTHON -m pip install -r requirements-build.txt" >&2
  exit 12
}

echo "[3/8] 生成图标（.ico + .icns）"
"$PYTHON" make_icon.py
if command -v iconutil >/dev/null 2>&1; then
  iconutil -c icns assets/app.iconset -o assets/app.icns
  echo "      assets/app.icns 已生成"
else
  echo "[WARN] 未找到 iconutil，将不使用自定义图标"
fi

echo "[4/8] 检查捆绑 FFmpeg"
if [ ! -x vendor/bin-macos/ffmpeg ]; then
  echo "[ERROR] 缺少 vendor/bin-macos/ffmpeg" >&2
  echo "        可用: $PYTHON -m pip install imageio-ffmpeg" >&2
  echo "        然后: mkdir -p vendor/bin-macos && cp \"\$($PYTHON -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')\" vendor/bin-macos/ffmpeg" >&2
  exit 13
fi

echo "[5/8] 清理旧产物"
rm -rf build dist "$APPNAME.spec"

echo "[6/8] 调用 PyInstaller（可能需要几分钟）"
ICON_ARG=""
[ -f assets/app.icns ] && ICON_ARG="--icon assets/app.icns"

# macOS 的 --add-data 分隔符是 ":"（Windows 是 ";"）
"$PYTHON" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "$APPNAME" \
  $ICON_ARG \
  --osx-bundle-identifier "com.youtubedownloader.app" \
  --paths . \
  --collect-all streamlit \
  --copy-metadata streamlit \
  --copy-metadata altair \
  --collect-all yt_dlp \
  --collect-all webview \
  --add-data "app.py:." \
  --add-data "vendor/bin-macos:bin" \
  --add-data "assets:assets" \
  --exclude-module pytest \
  desktop_entry.py

echo "[7/8] ad-hoc 签名（Apple Silicon 要求可执行文件至少有签名）"
APP="dist/$APPNAME.app"
if command -v codesign >/dev/null 2>&1; then
  # 注意：ad-hoc 签名（-）不是开发者证书，Gatekeeper 仍会提示"无法验证开发者"，
  # 但能避免 arm64 上因缺少签名导致的"已损坏/被系统杀掉"问题。
  if codesign --force --deep --sign - "$APP" 2>/dev/null; then
    codesign --verify --deep --strict "$APP" 2>/dev/null \
      && echo "      ad-hoc 签名完成并校验通过" \
      || echo "[WARN] 签名完成但校验未通过（多数情况下仍可运行）"
  else
    echo "[WARN] ad-hoc 签名失败；Apple Silicon 上可能无法启动"
  fi
else
  echo "[WARN] 未找到 codesign，跳过签名"
fi

echo "[8/8] 校验产物"
if [ ! -x "$APP/Contents/MacOS/$APPNAME" ]; then
  echo "[ERROR] 缺少主可执行文件 $APP/Contents/MacOS/$APPNAME" >&2
  exit 14
fi
if ! find "$APP" -name app.py | grep -q .; then
  echo "[ERROR] 应用包内找不到 app.py" >&2
  exit 14
fi
if ! find "$APP" -name ffmpeg -type f | grep -q .; then
  echo "[ERROR] 应用包内找不到捆绑的 ffmpeg" >&2
  exit 14
fi

echo
echo "构建完成: $APP"
echo "自检:   \"$APP/Contents/MacOS/$APPNAME\" --self-check"
echo "冒烟:   \"$APP/Contents/MacOS/$APPNAME\" --smoke-test"
echo "分发前请阅读 DEPLOY.md 的「macOS」一节（Gatekeeper / 架构 / 签名）"
