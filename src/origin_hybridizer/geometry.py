from __future__ import annotations

from dataclasses import dataclass
import math
import re


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def normalized(self) -> "Rect":
        x1, x2 = sorted((self.x, self.right))
        y1, y2 = sorted((self.y, self.bottom))
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def contains_point(self, x: float, y: float) -> bool:
        return self.x <= x <= self.right and self.y <= y <= self.bottom

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right < other.x
            or other.right < self.x
            or self.bottom < other.y
            or other.bottom < self.y
        )

    def expanded(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x - dx, self.y - dy, self.width + 2 * dx, self.height + 2 * dy)

    def clipped(self, other: "Rect") -> "Rect":
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2, y2 = min(self.right, other.right), min(self.bottom, other.bottom)
        return Rect(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))

    @classmethod
    def union_all(cls, rects: list["Rect"]) -> "Rect":
        if not rects:
            raise ValueError("At least one rectangle is required")
        x1 = min(r.x for r in rects)
        y1 = min(r.y for r in rects)
        x2 = max(r.right for r in rects)
        y2 = max(r.bottom for r in rects)
        return cls(x1, y1, x2 - x1, y2 - y1)


# SVG affine matrices are (a, b, c, d, e, f), mapping
# x' = ax + cy + e and y' = bx + dy + f.
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(left: Matrix, right: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def inverse(matrix: Matrix) -> Matrix:
    a, b, c, d, e, f = matrix
    determinant = a * d - b * c
    if abs(determinant) < 1e-14:
        raise ValueError("The SVG contains a non-invertible transform")
    return (
        d / determinant,
        -b / determinant,
        -c / determinant,
        a / determinant,
        (c * f - d * e) / determinant,
        (b * e - a * f) / determinant,
    )


_TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
_NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def parse_transform(value: str | None) -> Matrix:
    result = IDENTITY
    if not value:
        return result
    for name, arguments in _TRANSFORM_RE.findall(value):
        nums = [float(v) for v in _NUMBER_RE.findall(arguments)]
        name = name.lower()
        if name == "matrix" and len(nums) == 6:
            current = tuple(nums)  # type: ignore[assignment]
        elif name == "translate" and nums:
            current = (1.0, 0.0, 0.0, 1.0, nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale" and nums:
            sy = nums[1] if len(nums) > 1 else nums[0]
            current = (nums[0], 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and nums:
            angle = math.radians(nums[0])
            rotation = (math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle), 0.0, 0.0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                current = multiply(
                    multiply((1.0, 0.0, 0.0, 1.0, cx, cy), rotation),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                )
            else:
                current = rotation
        elif name == "skewx" and nums:
            current = (1.0, 0.0, math.tan(math.radians(nums[0])), 1.0, 0.0, 0.0)
        elif name == "skewy" and nums:
            current = (1.0, math.tan(math.radians(nums[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        result = multiply(result, current)
    return result


def matrix_string(matrix: Matrix) -> str:
    values = " ".join(f"{v:.12g}" for v in matrix)
    return f"matrix({values})"


def map_rect(matrix: Matrix, rect: Rect) -> Rect:
    a, b, c, d, e, f = matrix
    points = [
        (rect.x, rect.y),
        (rect.right, rect.y),
        (rect.right, rect.bottom),
        (rect.x, rect.bottom),
    ]
    mapped = [(a * x + c * y + e, b * x + d * y + f) for x, y in points]
    xs = [point[0] for point in mapped]
    ys = [point[1] for point in mapped]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


_LENGTH_RE = re.compile(r"^\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)\s*([A-Za-z%]*)\s*$")


def length_to_inches(value: str | None, fallback_px: float) -> float:
    if not value:
        return fallback_px / 96.0
    match = _LENGTH_RE.match(value)
    if not match:
        return fallback_px / 96.0
    number, unit = float(match.group(1)), match.group(2).lower()
    factors = {
        "": 1 / 96.0,
        "px": 1 / 96.0,
        "in": 1.0,
        "cm": 1 / 2.54,
        "mm": 1 / 25.4,
        "pt": 1 / 72.0,
        "pc": 1 / 6.0,
        "q": 1 / 101.6,
    }
    return number * factors.get(unit, 1 / 96.0)
