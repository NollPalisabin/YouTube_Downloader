"""生成应用图标 —— 可复现（不含外部美术资源），构建前跑一次即可。

输出：
- ``assets/app.ico``         Windows exe 图标（多尺寸）
- ``assets/app.iconset/``    macOS 的 .iconset 目录，供 ``iconutil -c icns`` 转成 .icns

图形：品牌红圆角方块 + 白色播放三角，缩到 16×16 仍可辨识。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

#: 以 256 设计、按 1024 渲染母版，再等比缩放出各尺寸，避免放大发虚
DESIGN_SIZE = 256
MASTER_SIZE = 1024

ACCENT = (225, 29, 72, 255)  # #E11D48
WHITE = (255, 255, 255, 255)

ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

#: macOS .iconset 要求的文件名 -> 像素尺寸
ICONSET_FILES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def render_master() -> Image.Image:
    """按 1024 px 渲染母版（256 设计等比放大 4 倍）。"""
    scale = MASTER_SIZE / DESIGN_SIZE
    image = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = 10 * scale
    radius = 56 * scale
    draw.rounded_rectangle(
        [margin, margin, MASTER_SIZE - margin, MASTER_SIZE - margin],
        radius=radius,
        fill=ACCENT,
    )

    # 播放三角（视觉重心略偏右，符合视频播放图标习惯）
    draw.polygon(
        [
            (104 * scale, 74 * scale),
            (104 * scale, 174 * scale),
            (188 * scale, 124 * scale),
        ],
        fill=WHITE,
    )
    return image


def render(size: int = DESIGN_SIZE) -> Image.Image:
    return render_master().resize((size, size), Image.LANCZOS)


def write_iconset(master: Image.Image, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, pixels in ICONSET_FILES.items():
        target = directory / filename
        master.resize((pixels, pixels), Image.LANCZOS).save(target, format="PNG")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    base = Path(args[0]).parent if args else Path(__file__).resolve().parent / "assets"
    base.mkdir(parents=True, exist_ok=True)

    master = render_master()

    ico_path = base / "app.ico"
    master.resize((256, 256), Image.LANCZOS).save(ico_path, format="ICO", sizes=ICON_SIZES)
    print(f"已生成图标：{ico_path}（{ico_path.stat().st_size / 1024:.1f} KB，{len(ICON_SIZES)} 种尺寸）")

    iconset_dir = base / "app.iconset"
    files = write_iconset(master, iconset_dir)
    print(f"已生成 macOS iconset：{iconset_dir}（{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
