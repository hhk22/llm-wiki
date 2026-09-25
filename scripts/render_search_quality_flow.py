"""Render the blog diagram: python scripts/render_search_quality_flow.py.

Requires Pillow and a Korean font (set DIAGRAM_FONT / DIAGRAM_FONT_BOLD
on systems without Windows Malgun Gothic).
"""

import math
import os
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCALE = 2
image = Image.new("RGB", (900 * SCALE, 1100 * SCALE), "white")
draw = ImageDraw.Draw(image)
INK = "#20314B"
BLUE = "#346BD4"
ORANGE = "#B96B20"
GRAY = "#6C7C90"
FONT = os.environ.get("DIAGRAM_FONT", "C:/Windows/Fonts/malgun.ttf")
BOLD = os.environ.get("DIAGRAM_FONT_BOLD", FONT if "DIAGRAM_FONT" in os.environ else "C:/Windows/Fonts/malgunbd.ttf")


def text(x, y, label, size=23, color=INK, bold=False):
    draw.text((x * SCALE, y * SCALE), label,
              font=ImageFont.truetype(BOLD if bold else FONT, size * SCALE),
              fill=color, anchor="mm")


def rect(bounds, fill, outline=None, radius=16):
    draw.rounded_rectangle(tuple(v * SCALE for v in bounds),
                           radius=radius * SCALE, fill=fill,
                           outline=outline, width=2 * SCALE)


def arrow(points, color=BLUE, dashed=False):
    for (x1, y1), (x2, y2) in pairwise(points):
        length = math.hypot(x2 - x1, y2 - y1)
        if dashed:
            for start in range(0, int(length), 15):
                end = min(start + 8, length)
                draw.line(((x1 + (x2 - x1) * start / length) * SCALE,
                           (y1 + (y2 - y1) * start / length) * SCALE,
                           (x1 + (x2 - x1) * end / length) * SCALE,
                           (y1 + (y2 - y1) * end / length) * SCALE),
                          fill=color, width=3 * SCALE)
        else:
            draw.line((x1 * SCALE, y1 * SCALE, x2 * SCALE, y2 * SCALE),
                      fill=color, width=3 * SCALE)
    x, y = points[-1]
    px, py = points[-2]
    angle = math.atan2(y - py, x - px)
    head = [(x, y)] + [(x - 12 * math.cos(angle + a), y - 12 * math.sin(angle + a))
                       for a in (-0.45, 0.45)]
    draw.polygon([(a * SCALE, b * SCALE) for a, b in head], fill=color)


def node(x, y, w, h, label, fill="#F1F6FF", color=BLUE, subtitle=None):
    rect((x - w / 2, y - h / 2, x + w / 2, y + h / 2), fill, color)
    text(x, y - 14 if subtitle else y, label, bold=True)
    if subtitle:
        text(x, y + 21, subtitle, size=20, color=GRAY)


# The right-hand lane is optional; the default vector path stays on the left.
rect((505, 240, 865, 635), "#FFFAF3", radius=22)

arrow([(450, 103), (450, 143)])
arrow([(450, 217), (450, 235), (260, 235), (260, 393)])
arrow([(450, 217), (450, 235), (685, 235), (685, 304)], ORANGE, True)
arrow([(685, 366), (685, 393)], ORANGE, True)
arrow([(685, 457), (685, 517)], ORANGE, True)
arrow([(405, 425), (460, 425), (460, 560), (535, 560)], ORANGE, True)
arrow([(260, 457), (260, 655)])
arrow([(685, 603), (685, 690), (420, 690)], ORANGE, True)
arrow([(260, 725), (260, 756), (450, 756), (450, 787)])
arrow([(450, 873), (450, 911)])
arrow([(450, 981), (450, 1015)])

rect((175, 247, 345, 287), "white", radius=0)
text(260, 267, "기본 경로", size=20, color=BLUE, bold=True)
rect((555, 247, 815, 287), "#FFFAF3", radius=0)
text(685, 267, "hybrid 비교 옵션", size=20, color=ORANGE, bold=True)

node(450, 70, 260, 66, "원래 질문", fill="#F6F8FB", color="#A0ADBE")
node(450, 180, 480, 74, "문서 유형·버전 조건 판단")
node(260, 425, 290, 64, "벡터 검색")
node(685, 335, 300, 62, "키워드용 식별자 보정", "#FFF5E6", ORANGE)
node(685, 425, 300, 64, "키워드 검색", "#FFF5E6", ORANGE)
node(685, 560, 300, 86, "가중 RRF 결합", "#FFF5E6", ORANGE,
     "키워드 1 : 벡터 3")
node(260, 690, 320, 70, "문서 Top 3")
node(450, 830, 660, 86, "정책 원인 질문: 참조 장애 보강",
     subtitle="문서 Top 3 안에 포함")
node(450, 946, 500, 70, "문서당 최대 2개 청크")
node(450, 1048, 580, 66, "근거 번호를 붙인 답변 하나",
     fill="#EDF8F5", color="#378875")

output = Path(__file__).resolve().parents[1] / "images" / "search-quality-flow-v2.png"
image.save(output, optimize=True)
print(output)
