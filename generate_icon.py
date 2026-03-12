#!/usr/bin/env python3
"""
Download an MDI icon from pictogrammers.com and convert it to the 1bpp BMP
format used by the e-ink display project.

Usage:
  python3 generate_icon.py <icon-name> [--type <notification|weather|other>] [--sizes <128,64,36>]

Examples:
  # Generate all three notification sizes for battery-charging:
  python3 generate_icon.py battery-charging --type notification

  # Generate a single custom size:
  python3 generate_icon.py thermometer --sizes 36 --out icon-bmp/weather-small/thermometer2.bmp

The SVG is fetched from the MDI GitHub repository:
  https://raw.githubusercontent.com/Templarian/MaterialDesign/master/svg/<name>.svg

Output BMP format:
  - 1bpp palette BMP
  - Palette index 0 = black (background/transparent)
  - Palette index 1 = white (drawn icon pixel)
  This matches the 'invert_alpha: true' convention used in waveshare.yaml.
"""

import argparse
import io
import os
import struct
import sys
import urllib.request


try:
    import cairosvg
except ImportError:
    sys.exit("cairosvg is required: pip install cairosvg")

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")


MDI_SVG_URL = "https://raw.githubusercontent.com/Templarian/MaterialDesign/master/svg/{name}.svg"

# Notification icon sizes matched to the three subdirectories
NOTIFICATION_SIZES = {
    "notification-large": 128,
    "notification-med": 64,
    "notification-small": 36,
}


def fetch_svg(icon_name: str) -> bytes:
    url = MDI_SVG_URL.format(name=icon_name)
    print(f"Fetching: {url}")
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"Failed to download icon '{icon_name}': HTTP {e.code}\n"
                 f"Check the name at https://pictogrammers.com/library/mdi/")


def svg_to_1bpp_bmp(svg_bytes: bytes, size: int) -> Image.Image:
    """Render SVG to an RGBA PNG then convert to 1bpp BMP.

    Convention: alpha > 0 (visible icon pixel) → palette index 1 (white)
                alpha == 0 (background)         → palette index 0 (black)
    This matches 'invert_alpha: true' in ESPHome.
    """
    png_bytes = cairosvg.svg2png(
        bytestring=svg_bytes,
        output_width=size,
        output_height=size,
    )
    rgba = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    # Build a 1bpp palette image manually:
    # index 0 = black (BG), index 1 = white (icon)
    bmp = Image.new("P", (size, size), 0)
    palette = [0, 0, 0] + [255, 255, 255] + [0] * (256 * 3 - 6)
    bmp.putpalette(palette)

    pixels = bmp.load()
    rgba_pixels = rgba.load()
    for y in range(size):
        for x in range(size):
            r, g, b, a = rgba_pixels[x, y]
            # Any visible pixel (non-transparent) becomes index 1
            pixels[x, y] = 1 if a > 128 else 0

    return bmp


def save_1bpp_bmp(img: Image.Image, path: str):
    """Write a true 1bpp BMP matching the project's existing icon format.

    Palette convention (matches existing icons):
      Color index 0 = white (0xFF 0xFF 0xFF) = background / transparent
      Color index 1 = black (0x00 0x00 0x00) = drawn icon pixel
    Pixel data: bit 1 = icon pixel, bit 0 = background (MSB first per byte).
    Rows are stored bottom-to-top (positive height in header).
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    w, h = img.size
    # Row stride: bits per row rounded up to a multiple of 32 bits (4 bytes)
    row_stride = ((w + 31) // 32) * 4

    # Build pixel data (bottom-to-top row order)
    pixel_data = bytearray(row_stride * h)
    pix = img.load()
    for y in range(h):
        bmp_row = h - 1 - y  # BMP rows are stored bottom-to-top
        for x in range(w):
            if pix[x, y] == 1:  # icon pixel
                byte_idx = bmp_row * row_stride + x // 8
                bit_shift = 7 - (x % 8)
                pixel_data[byte_idx] |= (1 << bit_shift)

    # BMP file header (14 bytes) + BITMAPINFOHEADER (40 bytes)
    # + 2-color palette (2 × 4 bytes) + pixel data
    pixel_data_offset = 14 + 40 + 2 * 4
    file_size = pixel_data_offset + len(pixel_data)

    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_data_offset)
    dib_header = struct.pack(
        "<IiiHHIIiiII",
        40,           # DIB header size
        w, h,         # width, height (positive = bottom-to-top)
        1,            # color planes
        1,            # bits per pixel
        0,            # compression (none)
        len(pixel_data),  # image size
        2835, 2835,   # pixels per meter X/Y (~72 DPI)
        2,            # colors in table
        0,            # important colors (0 = all)
    )
    # Palette: color 0 = white (background), color 1 = black (icon pixel)
    palette = (
        struct.pack("<BBBB", 255, 255, 255, 0) +  # index 0: white
        struct.pack("<BBBB", 0, 0, 0, 0)           # index 1: black
    )

    with open(path, "wb") as f:
        f.write(file_header)
        f.write(dib_header)
        f.write(palette)
        f.write(pixel_data)

    print(f"  Saved: {path}  ({os.path.getsize(path)} bytes, 1bpp)")


def main():
    parser = argparse.ArgumentParser(description="Generate 1bpp BMP icons from MDI SVGs")
    parser.add_argument("icon_name", help="MDI icon name, e.g. battery-charging")
    parser.add_argument(
        "--type",
        choices=["notification", "weather", "other"],
        default="notification",
        help="Icon type: 'notification' generates all three sizes into the notification-* dirs (default)",
    )
    parser.add_argument(
        "--sizes",
        help="Comma-separated pixel sizes, e.g. 128,64,36 (overrides --type sizes)",
    )
    parser.add_argument(
        "--out",
        help="Output file path (only valid with a single --sizes value)",
    )
    parser.add_argument(
        "--base-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Base directory of the project (default: directory of this script)",
    )
    args = parser.parse_args()

    svg_bytes = fetch_svg(args.icon_name)

    if args.sizes:
        sizes = [int(s.strip()) for s in args.sizes.split(",")]
        if args.out:
            if len(sizes) != 1:
                sys.exit("--out requires exactly one size in --sizes")
            img = svg_to_1bpp_bmp(svg_bytes, sizes[0])
            save_1bpp_bmp(img, args.out)
        else:
            for sz in sizes:
                img = svg_to_1bpp_bmp(svg_bytes, sz)
                out = os.path.join(args.base_dir, "icon-bmp", f"{args.icon_name}-{sz}.bmp")
                save_1bpp_bmp(img, out)
    elif args.type == "notification":
        for subdir, size in NOTIFICATION_SIZES.items():
            img = svg_to_1bpp_bmp(svg_bytes, size)
            out = os.path.join(
                args.base_dir, "icon-bmp", subdir, f"{args.icon_name}.bmp"
            )
            save_1bpp_bmp(img, out)
    else:
        parser.error("Specify --sizes or use --type notification")


if __name__ == "__main__":
    main()
