"""Generate the two bitmaps the WiX installer shows (banner + dialog picture).

WiX wants 24-bit BMPs at fixed sizes: 493x58 for the banner, 493x312 for the
dialog picture.  Run with the bundled Python (it has Pillow).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ICONS = Path(__file__).resolve().parents[1] / "app" / "src-tauri" / "icons"
BLUE = (0, 76, 216)
INK = (17, 24, 39)
MUTED = (71, 85, 105)
FONT_DIR = Path("C:/Windows/Fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (name, "segoeuib.ttf", "segoeui.ttf", "arialbd.ttf"):
        path = FONT_DIR / candidate
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise SystemExit(f"no font found in {FONT_DIR}")


def _mark(size: int) -> Image.Image:
    """The app icon, trimmed to a square and scaled."""
    icon = Image.open(ICONS / "icon.png").convert("RGBA")
    side = min(icon.width, icon.height)
    left = (icon.width - side) // 2
    top = (icon.height - side) // 2
    return icon.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)


def banner() -> Image.Image:
    img = Image.new("RGB", (493, 58), "white")
    img.paste(_mark(40), (12, 9), _mark(40))
    draw = ImageDraw.Draw(img)
    draw.text((62, 14), "LitLoop", font=_font("seguisb.ttf", 22), fill=INK)
    draw.text((150, 20), "学术文献 Agent", font=_font("msyh.ttc", 15), fill=MUTED)
    draw.rectangle([0, 55, 493, 58], fill=BLUE)
    return img


def dialog() -> Image.Image:
    # WiX WixUI_InstallDir places black title/body text on the right (x >= 175)
    # and expects a 164x312 brand panel on the left.
    panel_w = 164
    img = Image.new("RGB", (493, 312), "white")
    draw = ImageDraw.Draw(img)
    for y in range(312):  # vertical gradient on the left brand panel
        t = y / 311
        draw.line([(0, y), (panel_w - 1, y)], fill=(int(0 + 8 * t), int(76 - 30 * t), int(216 - 90 * t)))
    draw.line([(panel_w, 0), (panel_w, 311)], fill=(226, 232, 240))
    mark = _mark(72)
    img.paste(mark, ((panel_w - 72) // 2, 34), mark)
    draw.text((22, 124), "LitLoop", font=_font("seguisb.ttf", 26), fill="white")
    draw.text((22, 166), "检索 · 筛选", font=_font("msyh.ttc", 13), fill=(222, 232, 255))
    draw.text((22, 188), "下载 · Zotero", font=_font("msyh.ttc", 13), fill=(222, 232, 255))
    draw.text((22, 222), "学术文献 Agent", font=_font("msyh.ttc", 13), fill=(195, 215, 252))
    draw.text((22, 282), "litloop-paper-agent", font=_font("segoeui.ttf", 11), fill=(170, 196, 245))
    return img


def main() -> None:
    banner().save(ICONS / "wix-banner.bmp")
    dialog().save(ICONS / "wix-dialog.bmp")
    for name in ("wix-banner.bmp", "wix-dialog.bmp"):
        path = ICONS / name
        with Image.open(path) as opened:
            print(f"{name}: {opened.width}x{opened.height} {opened.mode} {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
