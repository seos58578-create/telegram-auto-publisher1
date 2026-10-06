import os
import re
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageFont


# =========================================================
# 配置
# =========================================================

OCR_LANG = os.getenv("OCR_LANG", "eng+por+chi_sim")
OCR_SCALE = int(os.getenv("OCR_SCALE", "2"))
OCR_MIN_CONF = float(os.getenv("OCR_MIN_CONF", "35"))

MY_TELEGRAM = os.getenv("MY_TELEGRAM", "").strip()
MY_WHATSAPP = os.getenv("MY_WHATSAPP", "").strip()
MY_PHONE = os.getenv("MY_PHONE", "").strip()
MY_WEBSITE = os.getenv("MY_WEBSITE", "").strip()

CONTACT_KEYWORDS = [
    "telegram",
    "telegram:",
    "tg",
    "tg:",
    "whatsapp",
    "whatsapp:",
    "whatapp",
    "wa",
    "wa:",
    "contact",
    "contact:",
    "phone",
    "phone:",
    "tel",
    "tel:",
    "telephone",
    "mobile",
    "line",
    "客服",
    "联系",
    "联系方式",
    "咨询",
    "联系我",
]

URL_RE = re.compile(
    r"(https?://|www\.)[^\s]+",
    re.IGNORECASE
)

TELEGRAM_RE = re.compile(
    r"(?<!\w)@[A-Za-z0-9_]{4,32}"
)

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d[\d\s().-]{6,}\d)(?!\d)"
)

WHATSAPP_RE = re.compile(
    r"(?:wa\.me/|whatsapp(?:\s+me)?[:\s]*)[\w+./-]+",
    re.IGNORECASE
)


# =========================================================
# 获取字体
# =========================================================

def get_font(size=28):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]

    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass

    return ImageFont.load_default()


# =========================================================
# 自己的联系方式
# =========================================================

def build_replacement_lines():
    lines = []

    if MY_TELEGRAM:
        value = MY_TELEGRAM
        if value.startswith("http"):
            lines.append(f"Telegram: {value}")
        elif value.startswith("@"):
            lines.append(f"Telegram: {value}")
        else:
            lines.append(f"Telegram: @{value}")

    if MY_WHATSAPP:
        value = MY_WHATSAPP

        if "wa.me" in value.lower():
            lines.append(f"WhatsApp: {value}")
        else:
            lines.append(f"WhatsApp: {value}")

    if MY_PHONE:
        lines.append(f"Phone: {MY_PHONE}")

    if MY_WEBSITE:
        lines.append(f"Web: {MY_WEBSITE}")

    return lines


# =========================================================
# OCR
# =========================================================

def ocr_image(image_path):
    image = cv2.imread(str(image_path))

    if image is None:
        return []

    height, width = image.shape[:2]

    # 放大
    if OCR_SCALE > 1:
        image = cv2.resize(
            image,
            None,
            fx=OCR_SCALE,
            fy=OCR_SCALE,
            interpolation=cv2.INTER_CUBIC
        )

    # 灰度
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 对比度增强
    gray = cv2.normalize(
        gray,
        None,
        0,
        255,
        cv2.NORM_MINMAX
    )

    # 轻微降噪
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # OCR
    try:
        data = pytesseract.image_to_data(
            gray,
            lang=OCR_LANG,
            config="--psm 11",
            output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        print(f"[OCR] failed: {e}")
        return []

    results = []

    count = len(data["text"])

    for i in range(count):
        text = (data["text"][i] or "").strip()

        if not text:
            continue

        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = 0

        if conf < OCR_MIN_CONF:
            continue

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])

        results.append({
            "text": text,
            "conf": conf,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "line_num": data["line_num"][i],
            "block_num": data["block_num"][i],
        })

    return results


# =========================================================
# 判断是否是联系方式
# =========================================================

def normalize_text(text):
    return re.sub(r"\s+", " ", text.strip())


def looks_like_contact(text):
    text = normalize_text(text)

    lower = text.lower()

    # Telegram
    if TELEGRAM_RE.search(text):
        return True

    # WhatsApp
    if WHATSAPP_RE.search(text):
        return True

    # URL
    if URL_RE.search(text):
        return True

    # 电话
    digits = re.sub(r"\D", "", text)

    if len(digits) >= 7:
        return True

    # 联系方式关键词
    for keyword in CONTACT_KEYWORDS:
        if keyword in lower:
            return True

    return False


# =========================================================
# 合并附近区域
# =========================================================

def boxes_close(a, b, gap=35):
    ax1 = a["x"]
    ay1 = a["y"]
    ax2 = a["x"] + a["w"]
    ay2 = a["y"] + a["h"]

    bx1 = b["x"]
    by1 = b["y"]
    bx2 = b["x"] + b["w"]
    by2 = b["y"] + b["h"]

    horizontal = (
        ax1 <= bx2 + gap and
        bx1 <= ax2 + gap
    )

    vertical = (
        ay1 <= by2 + gap and
        by1 <= ay2 + gap
    )

    return horizontal and vertical


def merge_boxes(boxes):
    if not boxes:
        return []

    merged = []

    for box in boxes:
        added = False

        for i, current in enumerate(merged):
            if boxes_close(current, box):
                x1 = min(current["x"], box["x"])
                y1 = min(current["y"], box["y"])

                x2 = max(
                    current["x"] + current["w"],
                    box["x"] + box["w"]
                )

                y2 = max(
                    current["y"] + current["h"],
                    box["y"] + box["h"]
                )

                merged[i] = {
                    "x": x1,
                    "y": y1,
                    "w": x2 - x1,
                    "h": y2 - y1,
                }

                added = True
                break

        if not added:
            merged.append({
                "x": box["x"],
                "y": box["y"],
                "w": box["w"],
                "h": box["h"],
            })

    # 再合并一轮
    changed = True

    while changed:
        changed = False
        new_boxes = []

        while merged:
            current = merged.pop(0)

            found = False

            for i, other in enumerate(merged):
                if boxes_close(current, other, gap=50):
                    x1 = min(current["x"], other["x"])
                    y1 = min(current["y"], other["y"])

                    x2 = max(
                        current["x"] + current["w"],
                        other["x"] + other["w"]
                    )

                    y2 = max(
                        current["y"] + current["h"],
                        other["y"] + other["h"]
                    )

                    merged[i] = {
                        "x": x1,
                        "y": y1,
                        "w": x2 - x1,
                        "h": y2 - y1,
                    }

                    changed = True
                    found = True
                    break

            if not found:
                new_boxes.append(current)

        merged = new_boxes

    return merged


# =========================================================
# OCR 联系方式检测
# =========================================================

def detect_contact_regions(ocr_results):
    contact_boxes = []

    for item in ocr_results:
        text = item["text"]

        if looks_like_contact(text):
            contact_boxes.append(item)

    if not contact_boxes:
        return []

    return merge_boxes(contact_boxes)


# =========================================================
# 选择替换背景
# =========================================================

def inpaint_region(image, region):
    h, w = image.shape[:2]

    x = max(0, region["x"])
    y = max(0, region["y"])

    rw = region["w"]
    rh = region["h"]

    x2 = min(w, x + rw)
    y2 = min(h, y + rh)

    if x2 <= x or y2 <= y:
        return image

    mask = np.zeros(
        image.shape[:2],
        dtype=np.uint8
    )

    # 稍微扩大区域
    pad = max(8, int(min(rw, rh) * 0.15))

    mx1 = max(0, x - pad)
    my1 = max(0, y - pad)
    mx2 = min(w, x2 + pad)
    my2 = min(h, y2 + pad)

    mask[my1:my2, mx1:mx2] = 255

    try:
        result = cv2.inpaint(
            image,
            mask,
            5,
            cv2.INPAINT_TELEA
        )

        return result

    except Exception:
        return image


# =========================================================
# 绘制自己的联系方式
# =========================================================

def draw_replacement(
    image_path,
    contact_regions,
    output_path
):
    if not contact_regions:
        return False

    replacement_lines = build_replacement_lines()

    if not replacement_lines:
        print("[OCR] 没有设置自己的联系方式")
        return False

    image = cv2.imread(str(image_path))

    if image is None:
        return False

    # 先删除原联系方式
    for region in contact_regions:
        image = inpaint_region(
            image,
            region
        )

    rgb = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2RGB
    )

    pil = Image.fromarray(rgb)

    draw = ImageDraw.Draw(pil, "RGBA")

    font_size = max(
        20,
        min(
            42,
            int(pil.width / 32)
        )
    )

    font = get_font(font_size)

    # 计算统一联系方式区域
    x1 = min(
        region["x"]
        for region in contact_regions
    )

    y1 = min(
        region["y"]
        for region in contact_regions
    )

    x2 = max(
        region["x"] + region["w"]
        for region in contact_regions
    )

    y2 = max(
        region["y"] + region["h"]
        for region in contact_regions
    )

    # 如果区域太小，扩大
    padding_x = 18
    padding_y = 14

    line_height = font_size + 10

    text_width = 0

    for line in replacement_lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=font
        )

        text_width = max(
            text_width,
            bbox[2] - bbox[0]
        )

    box_width = text_width + padding_x * 2
    box_height = (
        line_height * len(replacement_lines)
        + padding_y * 2
    )

    # 尽量放在原联系方式附近
    center_x = (x1 + x2) // 2

    box_x1 = center_x - box_width // 2
    box_x2 = box_x1 + box_width

    box_y1 = y1
    box_y2 = box_y1 + box_height

    # 防止超出图片
    if box_x1 < 5:
        box_x1 = 5
        box_x2 = box_x1 + box_width

    if box_x2 > pil.width - 5:
        box_x2 = pil.width - 5
        box_x1 = box_x2 - box_width

    if box_y2 > pil.height - 5:
        box_y2 = pil.height - 5
        box_y1 = max(
            5,
            box_y2 - box_height
        )

    # 半透明背景
    draw.rounded_rectangle(
        (
            box_x1,
            box_y1,
            box_x2,
            box_y2
        ),
        radius=12,
        fill=(0, 0, 0, 185)
    )

    current_y = box_y1 + padding_y

    for line in replacement_lines:
        draw.text(
            (
                box_x1 + padding_x,
                current_y
            ),
            line,
            font=font,
            fill=(255, 255, 255, 255)
        )

        current_y += line_height

    output_rgb = np.array(pil)

    output_bgr = cv2.cvtColor(
        output_rgb,
        cv2.COLOR_RGB2BGR
    )

    cv2.imwrite(
        str(output_path),
        output_bgr,
        [
            int(cv2.IMWRITE_JPEG_QUALITY),
            95
        ]
    )

    return True


# =========================================================
# 主函数
# =========================================================

def replace_contacts(
    input_path,
    output_path
):
    print(f"[OCR] scanning: {input_path}")

    results = ocr_image(input_path)

    if not results:
        print("[OCR] no OCR result")
        return False

    print(
        "[OCR] text:",
        " | ".join(
            item["text"]
            for item in results
        )
    )

    regions = detect_contact_regions(
        results
    )

    if not regions:
        print("[OCR] no contact detected")
        return False

    print(
        f"[OCR] detected {len(regions)} contact region(s)"
    )

    return draw_replacement(
        input_path,
        regions,
        output_path
    )
