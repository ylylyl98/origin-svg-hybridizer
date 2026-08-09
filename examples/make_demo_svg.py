"""Create a small Origin-style all-vector demo figure."""

from pathlib import Path
import colorsys


def main() -> None:
    output = Path(__file__).with_name("origin_heatmap_demo.svg")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="6in" height="4in" viewBox="0 0 600 400">',
        '<rect x="0" y="0" width="600" height="400" fill="white"/>',
        '<g id="heatmap" transform="translate(10 5)">',
    ]
    cols, rows = 42, 28
    cell_w, cell_h = 8.0, 8.0
    for row in range(rows):
        for col in range(cols):
            value = (col / (cols - 1) * 0.72 + row / (rows - 1) * 0.28) % 1.0
            red, green, blue = colorsys.hsv_to_rgb(0.70 - 0.70 * value, 0.82, 0.93)
            color = f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"
            x, y = 125 + col * cell_w, 55 + row * cell_h
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w + 0.15}" height="{cell_h + 0.15}" fill="{color}"/>')
    parts.extend([
        '</g>',
        '<g id="axes" fill="none" stroke="#1b1b1b" stroke-width="1.5">',
        '<path d="M135 60 V284 H471"/><path d="M135 284 V290 M219 284 V290 M303 284 V290 M387 284 V290 M471 284 V290"/>',
        '<path d="M129 60 H135 M129 116 H135 M129 172 H135 M129 228 H135 M129 284 H135"/>',
        '</g>',
        '<g font-family="Arial" font-size="15" fill="#111">',
        '<text x="303" y="325" text-anchor="middle">Vbg (V)</text>',
        '<text x="52" y="175" text-anchor="middle" transform="rotate(-90 52 175)">Photon energy (eV)</text>',
        '<text x="135" y="307" text-anchor="middle">0</text><text x="303" y="307" text-anchor="middle">5</text><text x="471" y="307" text-anchor="middle">10</text>',
        '</g>',
        '<g id="annotation"><path d="M370 90 L410 115" fill="none" stroke="white" stroke-width="3"/><polygon points="410,115 398,112 405,103" fill="white"/></g>',
        '<text x="390" y="79" font-family="Arial" font-size="14" fill="white">feature</text>',
        '</svg>',
    ])
    output.write_text("\n".join(parts), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

