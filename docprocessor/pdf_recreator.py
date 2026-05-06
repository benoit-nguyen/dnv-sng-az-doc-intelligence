"""
PDF Recreation module.

Recreates PDFs from Azure Document Intelligence analysis JSON,
with optional in-place translation via Azure Translator Text API.
Handles both scanned PDFs (polygon coordinates) and DOCX (text-flow fallback).
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Font helpers

_KOREAN_FONT_REGISTERED = False


def _ensure_korean_font() -> str:
    """Register the Korean font once and return its name, falling back to Helvetica."""
    global _KOREAN_FONT_REGISTERED
    if not _KOREAN_FONT_REGISTERED:
        font_path = r"C:\Windows\Fonts\malgun.ttf"
        try:
            pdfmetrics.registerFont(TTFont("Malgun", font_path))
            _KOREAN_FONT_REGISTERED = True
            logger.info("Korean font registered: %s", font_path)
            return "Malgun"
        except Exception as exc:
            logger.warning("Could not register Korean font: %s", exc)
    return "Malgun" if _KOREAN_FONT_REGISTERED else "Helvetica"


# ---------------------------------------------------------------------------
# Azure Translator client (segment-level text translation)


class AzureTranslator:
    """Lightweight Azure Translator client with batching and retry."""

    def __init__(
        self,
        endpoint: str,
        *,
        key: str,
        region: Optional[str] = None,
        timeout: float = 15.0,
        max_batch: int = 25,
        max_chars: int = 4500,
        ca_bundle: Optional[str] = None,
        paths: Optional[Sequence[str]] = None,
    ) -> None:
        if not endpoint:
            raise ValueError("translator endpoint is required")
        if not key:
            raise ValueError("translator key is required")

        self._base_endpoint = endpoint.rstrip("/")
        self._key = key
        self._region = region
        self._timeout = timeout
        self._session = requests.Session()
        self._session.verify = ca_bundle or True
        self._max_batch = max(1, max_batch)
        self._max_chars = max(100, max_chars)
        if paths:
            self._paths = [self._normalize_path(p) for p in paths]
        else:
            self._paths = ["/translate", "/translator/text/v3.0/translate"]

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "AzureTranslator":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def translate_segments(self, segments: Sequence[str], locale: str) -> List[str]:
        """Translate the provided text segments into *locale*, returning results in order."""
        results: List[str] = ["" for _ in segments]
        buffer: List[str] = []
        buffer_indices: List[int] = []
        buffer_chars = 0

        for index, text in enumerate(segments):
            if not text:
                continue
            buffer.append(text)
            buffer_indices.append(index)
            buffer_chars += len(text)
            if len(buffer) >= self._max_batch or buffer_chars >= self._max_chars:
                translated = self._dispatch(buffer, locale)
                for target_index, translated_text in zip(buffer_indices, translated):
                    results[target_index] = translated_text
                buffer = []
                buffer_indices = []
                buffer_chars = 0

        if buffer:
            translated = self._dispatch(buffer, locale)
            for target_index, translated_text in zip(buffer_indices, translated):
                results[target_index] = translated_text

        return results

    def _dispatch(self, texts: Sequence[str], locale: str) -> List[str]:
        payload = [{"text": text} for text in texts]
        params = {"api-version": "3.0", "to": locale}
        headers = {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": self._key,
        }
        if self._region:
            headers["Ocp-Apim-Subscription-Region"] = self._region

        last_response: Optional[requests.Response] = None
        for index, relative_path in enumerate(self._paths):
            url = f"{self._base_endpoint}{relative_path}"
            for attempt in range(3):
                response = self._session.post(
                    url,
                    params=params,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
                last_response = response
                if response.status_code in (429, 503) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                break
            if response.status_code == 404 and index + 1 < len(self._paths):
                continue
            response.raise_for_status()
            data = response.json()
            return [
                (item.get("translations") or [{}])[0].get("text", "")
                for item in data
            ]

        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("Translation failed: no reachable endpoint path")

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path if path.startswith("/") else "/" + path


# ---------------------------------------------------------------------------
# Translation helpers


def translate_analysis_content(
    analysis_data: dict,
    translator: AzureTranslator,
    locale: str,
) -> Tuple[int, int, int]:
    """Translate paragraph and table-cell text in *analysis_data* in-place.

    Returns ``(unique_segments, paragraph_updates, cell_updates)``.
    """
    paragraphs = analysis_data.get("paragraphs") or []
    tables = analysis_data.get("tables") or []

    unique_texts: dict = {}
    order: List[str] = []
    references: List[Tuple[str, Tuple[int, int], str]] = []

    for para_index, para in enumerate(paragraphs):
        content = (para.get("content") or "").strip()
        if not content:
            continue
        if content not in unique_texts:
            unique_texts[content] = ""
            order.append(content)
        references.append(("paragraph", (para_index, 0), content))

    for table_index, table in enumerate(tables):
        for cell_index, cell in enumerate(table.get("cells") or []):
            content = (cell.get("content") or "").strip()
            if not content:
                continue
            if content not in unique_texts:
                unique_texts[content] = ""
                order.append(content)
            references.append(("cell", (table_index, cell_index), content))

    if not order:
        return 0, 0, 0

    translations = translator.translate_segments(order, locale)
    for original, translated in zip(order, translations):
        unique_texts[original] = translated or original

    paragraph_updates = 0
    cell_updates = 0
    for ref_type, location, original_text in references:
        translated_text = unique_texts.get(original_text, original_text)
        if ref_type == "paragraph":
            paragraphs[location[0]]["content"] = translated_text
            paragraph_updates += 1
        else:
            tables[location[0]]["cells"][location[1]]["content"] = translated_text
            cell_updates += 1

    return len(order), paragraph_updates, cell_updates


# ---------------------------------------------------------------------------
# PDF layout helpers


def _get_bounding_box(polygon: List[float]) -> Optional[dict]:
    if not polygon or len(polygon) < 8:
        return None
    x_coords = [polygon[i] for i in range(0, len(polygon), 2)]
    y_coords = [polygon[i] for i in range(1, len(polygon), 2)]
    return {
        "x": min(x_coords),
        "y": min(y_coords),
        "x2": max(x_coords),
        "y2": max(y_coords),
        "width": max(x_coords) - min(x_coords),
        "height": max(y_coords) - min(y_coords),
    }


def _merge_text_on_same_line(paragraphs: list) -> list:
    """Merge standalone short tokens with the adjacent text on the same line."""
    if not paragraphs:
        return paragraphs
    merged = []
    skip = set()
    for i, para in enumerate(paragraphs):
        if i in skip:
            continue
        content = para.get("content", "").strip()
        regions = para.get("boundingRegions", [])
        if not regions or not content:
            merged.append(para)
            continue
        bbox = _get_bounding_box(regions[0].get("polygon", []))
        if not bbox:
            merged.append(para)
            continue
        if len(content) <= 3 and i + 1 < len(paragraphs):
            next_para = paragraphs[i + 1]
            next_regions = next_para.get("boundingRegions", [])
            if next_regions:
                next_bbox = _get_bounding_box(next_regions[0].get("polygon", []))
                if next_bbox:
                    y_diff = abs(bbox["y"] - next_bbox["y"])
                    x_gap = next_bbox["x"] - bbox["x2"]
                    if y_diff < 0.3 and 0 < x_gap < 3.0:
                        merged_para = next_para.copy()
                        merged_para["content"] = content + " " + next_para.get("content", "").strip()
                        merged.append(merged_para)
                        skip.add(i + 1)
                        continue
        merged.append(para)
    return merged


def _text_overlaps_table(text_bbox: dict, table_cells: list, threshold: float = 0.3) -> bool:
    if not text_bbox:
        return False
    text_area = text_bbox["width"] * text_bbox["height"]
    if text_area == 0:
        return False
    for cell in table_cells:
        cell_regions = cell.get("boundingRegions", [])
        if not cell_regions:
            continue
        cell_bbox = _get_bounding_box(cell_regions[0].get("polygon", []))
        if not cell_bbox:
            continue
        x_overlap = max(0, min(text_bbox["x2"], cell_bbox["x2"]) - max(text_bbox["x"], cell_bbox["x"]))
        y_overlap = max(0, min(text_bbox["y2"], cell_bbox["y2"]) - max(text_bbox["y"], cell_bbox["y"]))
        if (x_overlap * y_overlap) / text_area > threshold:
            return True
    return False


def _draw_table_with_grid(
    c: canvas.Canvas,
    table_data: dict,
    page_width: float,
    page_height: float,
    orig_width: float,
    orig_height: float,
    scale_x: float,
    scale_y: float,
    font_name: str,
) -> None:
    cells = table_data.get("cells", [])
    if not cells:
        return

    row_count = table_data.get("rowCount", 0)
    col_count = table_data.get("columnCount", 0)
    rows_info = [{"top": float("inf"), "bottom": float("-inf"), "has_content": False} for _ in range(row_count)]
    cols_info = [{"left": float("inf"), "right": float("-inf")} for _ in range(col_count)]

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    x_coords: List[float] = []
    y_coords: List[float] = []
    cell_data = []

    for cell in cells:
        regions = cell.get("boundingRegions", [])
        if not regions:
            continue
        bbox = _get_bounding_box(regions[0].get("polygon", []))
        if not bbox:
            continue
        x1 = bbox["x"] * inch * scale_x
        y1 = page_height - (bbox["y2"] * inch * scale_y)
        x2 = bbox["x2"] * inch * scale_x
        y2 = page_height - (bbox["y"] * inch * scale_y)
        min_x = min(min_x, x1); min_y = min(min_y, y1)
        max_x = max(max_x, x2); max_y = max(max_y, y2)
        x_coords += [x1, x2]
        y_coords += [y1, y2]
        cell_data.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "content": cell.get("content", "").strip()})
        ri = cell.get("rowIndex")
        rs = max(1, cell.get("rowSpan", 1))
        ci = cell.get("columnIndex")
        cs = max(1, cell.get("columnSpan", 1))
        has_text = bool(cell.get("content", "").strip())
        if ri is not None:
            for r in range(ri, min(row_count, ri + rs)):
                rows_info[r]["top"] = min(rows_info[r]["top"], y1)
                rows_info[r]["bottom"] = max(rows_info[r]["bottom"], y2)
                if has_text:
                    rows_info[r]["has_content"] = True
        if ci is not None:
            for cc in range(ci, min(col_count, ci + cs)):
                cols_info[cc]["left"] = min(cols_info[cc]["left"], x1)
                cols_info[cc]["right"] = max(cols_info[cc]["right"], x2)

    def _dedup(coords: List[float], tol: float = 6.0) -> List[float]:
        result: List[float] = []
        for v in sorted(coords):
            if result and abs(v - result[-1]) <= tol:
                continue
            result.append(v)
        return result

    valid_rows = [r for r in rows_info if r["top"] < float("inf")]
    last_content_row = max((i for i, r in enumerate(rows_info) if r["has_content"]), default=-1)
    if valid_rows and last_content_row >= 0:
        h_lines = [min(r["top"] for r in valid_rows)]
        for idx, row in enumerate(rows_info):
            if row["bottom"] == float("-inf"):
                continue
            if idx < last_content_row:
                h_lines.append(row["bottom"])
            elif idx == last_content_row:
                h_lines.append(max(r["bottom"] for r in valid_rows))
                break
        y_sorted = _dedup(h_lines)
    else:
        y_sorted = _dedup(y_coords)

    valid_cols = [col for col in cols_info if col["left"] < float("inf")]
    if valid_cols:
        v_lines = [min(col["left"] for col in valid_cols)]
        for col in cols_info:
            if col["right"] != float("-inf"):
                v_lines.append(col["right"])
        v_lines.append(max(col["right"] for col in valid_cols))
        x_sorted = _dedup(v_lines)
    else:
        x_sorted = _dedup(x_coords)

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    for y in y_sorted:
        c.line(min_x, y, max_x, y)
    for x in x_sorted:
        c.line(x, min_y, x, max_y)

    for cell_info in cell_data:
        content = cell_info["content"]
        if not content:
            continue
        x1, y1, x2, y2 = cell_info["x1"], cell_info["y1"], cell_info["x2"], cell_info["y2"]
        width, height = x2 - x1, y2 - y1
        font_size = max(6, min(10, height * 0.4))
        try:
            c.setFont(font_name, font_size)
        except Exception:
            c.setFont("Helvetica", font_size)
        c.setFillColor(colors.black)
        max_width = width - 6
        text_width = c.stringWidth(content, c._fontname, font_size)
        while text_width > max_width and font_size > 4:
            font_size -= 0.5
            try:
                c.setFont(font_name, font_size)
            except Exception:
                c.setFont("Helvetica", font_size)
            text_width = c.stringWidth(content, c._fontname, font_size)
        if text_width > max_width:
            while content and c.stringWidth(content + "...", c._fontname, font_size) > max_width:
                content = content[:-1]
            content += "..."
        text_x = x1 + (width - c.stringWidth(content, c._fontname, font_size)) / 2
        text_y = y1 + (height - font_size) / 2
        c.saveState()
        p = c.beginPath()
        p.rect(x1, y1, width, height)
        c.clipPath(p, stroke=0)
        c.drawString(text_x, text_y, content)
        c.restoreState()


# ---------------------------------------------------------------------------
# Public API


def create_pdf_from_analysis(
    json_file: str,
    output_pdf: str,
    translation_paragraphs: Optional[List[str]] = None,
    analysis_data: Optional[dict] = None,
) -> None:
    """Recreate a PDF from an Azure Document Intelligence analysis JSON.

    Parameters
    ----------
    json_file:
        Path to the ``.json`` analysis file (ignored when *analysis_data* supplied).
    output_pdf:
        Destination ``.pdf`` path.
    translation_paragraphs:
        Pre-translated paragraph strings to substitute in order.
    analysis_data:
        Already-loaded analysis dict; when given, *json_file* is not read.
    """
    font_name = _ensure_korean_font()

    if analysis_data is not None:
        data = analysis_data
    else:
        with open(json_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)

    # Unwrap batch result envelope when present
    if "analyzeResult" in data and "pages" not in data:
        data = data["analyzeResult"]

    pages = data.get("pages", [])
    tables = data.get("tables", [])
    paragraphs = data.get("paragraphs", [])

    if not pages:
        raise ValueError(f"No pages found in {json_file}")

    logger.info(
        "Processing %d pages, %d tables, %d paragraphs",
        len(pages), len(tables), len(paragraphs),
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    c = canvas.Canvas(output_pdf, pagesize=A4)
    page_width, page_height = A4
    translation_paragraphs = translation_paragraphs or []

    has_coordinates = any(p.get("boundingRegions") for p in paragraphs)

    # --- Text-flow path (DOCX / no polygon coordinates) ---
    if not has_coordinates:
        margin = 50
        font_size = 10
        line_height = font_size * 1.4
        x = margin
        y = page_height - margin
        t_idx = 0
        try:
            c.setFont(font_name, font_size)
        except Exception:
            c.setFont("Helvetica", font_size)
        c.setFillColor(colors.black)
        for para in paragraphs:
            content = para.get("content", "").strip()
            if not content:
                y -= line_height * 0.5
                continue
            if translation_paragraphs and t_idx < len(translation_paragraphs):
                content = translation_paragraphs[t_idx]
                t_idx += 1
            wrap_width = page_width - 2 * margin
            try:
                lines = simpleSplit(content, c._fontname, font_size, wrap_width)
            except Exception:
                lines = [content]
            for line in lines:
                if y < margin:
                    c.showPage()
                    y = page_height - margin
                    try:
                        c.setFont(font_name, font_size)
                    except Exception:
                        c.setFont("Helvetica", font_size)
                    c.setFillColor(colors.black)
                c.drawString(x, y, line)
                y -= line_height
            y -= line_height * 0.4
        c.save()
        file_size = os.path.getsize(output_pdf)
        logger.info("Created: %s (%.1f KB)", os.path.basename(output_pdf), file_size / 1024)
        return

    # --- Coordinate-based path (scanned PDF) ---
    t_idx = 0
    for page_idx, page in enumerate(pages):
        page_number = page.get("pageNumber", page_idx + 1)
        orig_width = (page.get("width") or 8.5) * inch
        orig_height = (page.get("height") or 11) * inch
        scale_x = page_width / orig_width
        scale_y = page_height / orig_height

        page_tables = [
            t for t in tables
            if (t.get("boundingRegions") or [{}])[0].get("pageNumber") == page_number
        ]
        all_table_cells = [cell for t in page_tables for cell in t.get("cells", [])]
        page_paragraphs = _merge_text_on_same_line([
            p for p in paragraphs
            if (p.get("boundingRegions") or [{}])[0].get("pageNumber") == page_number
        ])

        for table_data in page_tables:
            try:
                _draw_table_with_grid(
                    c, table_data, page_width, page_height,
                    orig_width, orig_height, scale_x, scale_y, font_name,
                )
            except Exception as exc:
                logger.warning("Could not draw table: %s", exc)

        margin = 36
        current_y = page_height - margin
        drawable = []
        for para in page_paragraphs:
            content = para.get("content", "").strip()
            if not content:
                continue
            regions = para.get("boundingRegions", [])
            if not regions:
                continue
            bbox = _get_bounding_box(regions[0].get("polygon", []))
            if not bbox or _text_overlaps_table(bbox, all_table_cells):
                continue
            x = max(margin, min(bbox["x"] * inch * scale_x, page_width - margin))
            top_y = page_height - (bbox["y"] * inch * scale_y)
            font_size = max(7, min(13, bbox["height"] * inch * scale_y * 0.9))
            translated = content
            if translation_paragraphs and t_idx < len(translation_paragraphs):
                translated = translation_paragraphs[t_idx]
                t_idx += 1
            drawable.append({"content": translated, "bbox": bbox, "x": x, "top_y": top_y, "font_size": font_size})

        drawable.sort(key=lambda item: (item["bbox"]["y"], item["bbox"]["x"]))

        for para_info in drawable:
            if current_y <= margin:
                break
            content = para_info["content"]
            font_size = para_info["font_size"]
            try:
                c.setFont(font_name, font_size)
            except Exception:
                c.setFont("Helvetica", font_size)
            c.setFillColor(colors.black)
            para_top = min(para_info["top_y"], current_y)
            if para_top <= margin:
                continue
            line_height = font_size * 1.25
            spacing = line_height * 0.6
            baseline_off = font_size * 0.2
            x_pos = para_info["x"]
            wrap_width = max(36, (page_width - margin) - x_pos)
            lines = simpleSplit(content, c._fontname, font_size, wrap_width)
            drawn = 0
            for i, line in enumerate(lines):
                ly = para_top - baseline_off - (i * line_height)
                if ly < margin:
                    break
                c.drawString(x_pos, ly, line)
                drawn += 1
            if drawn:
                current_y = max(margin, para_top - baseline_off - line_height * drawn - spacing)

        if page_idx < len(pages) - 1:
            c.showPage()

    c.save()
    file_size = os.path.getsize(output_pdf)
    logger.info("Created: %s (%.1f KB)", os.path.basename(output_pdf), file_size / 1024)


def build_translator_from_settings() -> AzureTranslator:
    """Create an :class:`AzureTranslator` using the current docprocessor settings."""
    settings = get_settings()
    endpoint = settings.translator_endpoint
    key = settings.translator_key
    region = settings.translator_region
    if not endpoint or not key:
        raise RuntimeError("TRANSLATOR_ENDPOINT and TRANSLATOR_KEY must be set in .env")
    raw_paths = os.getenv("TRANSLATOR_PATHS")
    paths = [s.strip() for s in raw_paths.split(",") if s.strip()] if raw_paths else None
    return AzureTranslator(
        endpoint,
        key=key,
        region=region,
        ca_bundle=settings.requests_ca_bundle,
        paths=paths,
    )
