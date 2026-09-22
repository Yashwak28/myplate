"""
ai_parser.py — Food parsing and photo analysis using Google Gemini 3.8 Flash.

Falls back to a keyword mock database if GEMINI_API_KEY is not set.
"""

import base64
import json
import logging
import mimetypes
import os
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gemini client (lazy init so missing key doesn't crash import)
# ---------------------------------------------------------------------------

_gemini_client = None


def _get_client():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        return None
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=api_key)
        return _gemini_client
    except Exception as exc:
        logger.error("Failed to initialise Gemini client: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clean_num(val) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        m = re.search(r"[-+]?\d*\.?\d+", val)
        if m:
            return float(m.group())
    return 0.0


def _sum_items(items: list) -> dict:
    clean = []
    for i in items:
        clean.append({
            "name":     str(i.get("name", "Food Item")),
            "calories": _clean_num(i.get("calories", 0)),
            "protein":  _clean_num(i.get("protein",  0)),
            "carbs":    _clean_num(i.get("carbs",    0)),
            "fats":     _clean_num(i.get("fats",     0)),
        })
    return {
        "calories": round(sum(i["calories"] for i in clean), 1),
        "protein":  round(sum(i["protein"]  for i in clean), 1),
        "carbs":    round(sum(i["carbs"]    for i in clean), 1),
        "fats":     round(sum(i["fats"]     for i in clean), 1),
        "items":    clean,
    }


def _extract_json(text: str) -> dict | None:
    """Robustly extract the first JSON object or list from model output."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Keyword mock — runs when GEMINI_API_KEY is not set
# ---------------------------------------------------------------------------

_MOCK_DB = {
    "chicken":   {"calories": 335, "protein": 42, "carbs": 0,  "fats": 16},
    "sandwich":  {"calories": 450, "protein": 25, "carbs": 48, "fats": 14},
    "burger":    {"calories": 550, "protein": 28, "carbs": 44, "fats": 28},
    "latte":     {"calories": 190, "protein": 7,  "carbs": 24, "fats": 7},
    "coffee":    {"calories":   5, "protein": 0,  "carbs": 1,  "fats": 0},
    "pizza":     {"calories": 570, "protein": 23, "carbs": 68, "fats": 22},
    "salad":     {"calories": 150, "protein": 8,  "carbs": 12, "fats": 8},
    "egg":       {"calories":  78, "protein": 6,  "carbs": 1,  "fats": 5},
    "rice":      {"calories": 206, "protein": 4,  "carbs": 45, "fats": 0},
    "banana":    {"calories":  89, "protein": 1,  "carbs": 23, "fats": 0},
    "yogurt":    {"calories": 100, "protein": 17, "carbs": 6,  "fats": 1},
    "roti":      {"calories": 120, "protein": 3,  "carbs": 23, "fats": 3},
    "dal":       {"calories": 180, "protein": 10, "carbs": 28, "fats": 3},
    "paneer":    {"calories": 265, "protein": 18, "carbs": 3,  "fats": 20},
    "dosa":      {"calories": 168, "protein": 4,  "carbs": 30, "fats": 4},
    "idli":      {"calories":  58, "protein": 2,  "carbs": 11, "fats": 0},
    "sambar":    {"calories":  80, "protein": 4,  "carbs": 12, "fats": 2},
    "biryani":   {"calories": 290, "protein": 10, "carbs": 38, "fats": 10},
    "noodles":   {"calories": 384, "protein": 8,  "carbs": 75, "fats": 5},
    "pasta":     {"calories": 371, "protein": 13, "carbs": 74, "fats": 2},
    "milk":      {"calories": 149, "protein": 8,  "carbs": 12, "fats": 8},
    "apple":     {"calories":  95, "protein": 0,  "carbs": 25, "fats": 0},
    "bread":     {"calories": 265, "protein": 9,  "carbs": 49, "fats": 3},
    "butter":    {"calories": 102, "protein": 0,  "carbs": 0,  "fats": 12},
    "tea":       {"calories":   2, "protein": 0,  "carbs": 0,  "fats": 0},
    "juice":     {"calories": 112, "protein": 1,  "carbs": 26, "fats": 0},
    "chocolate": {"calories": 546, "protein": 5,  "carbs": 60, "fats": 31},
    "chips":     {"calories": 547, "protein": 7,  "carbs": 57, "fats": 35},
    "fish":      {"calories": 206, "protein": 28, "carbs": 0,  "fats": 10},
    "mutton":    {"calories": 294, "protein": 25, "carbs": 0,  "fats": 21},
    "oats":      {"calories": 389, "protein": 17, "carbs": 66, "fats": 7},
    "upma":      {"calories": 200, "protein": 5,  "carbs": 32, "fats": 6},
    "poha":      {"calories": 180, "protein": 4,  "carbs": 36, "fats": 4},
    "paratha":   {"calories": 260, "protein": 6,  "carbs": 36, "fats": 10},
    "curd":      {"calories":  98, "protein": 11, "carbs": 3,  "fats": 4},
    "toast":     {"calories": 130, "protein": 4,  "carbs": 24, "fats": 2},
}


def _mock_parse(text: str) -> dict:
    words = re.findall(r"[a-z]+", text.lower())
    found = {}
    for word in words:
        if word in _MOCK_DB and word not in found:
            found[word] = _MOCK_DB[word]
    if not found:
        found["meal"] = {"calories": 400, "protein": 20, "carbs": 40, "fats": 15}
    items = [{"name": k.capitalize(), **v} for k, v in found.items()]
    result = _sum_items(items)
    result["notes"] = "Estimated using built-in food database (Gemini API key not configured)."
    return result


# ---------------------------------------------------------------------------
# Text parser — Gemini 3.8 Flash
# ---------------------------------------------------------------------------

_TEXT_PROMPT = """\
You are a nutrition expert. Parse the meal description below and return ONLY a valid JSON object.
Format:
{{
  "items": [
    {{"name": "Food Name", "calories": 300, "protein": 25, "carbs": 30, "fats": 8}}
  ],
  "notes": "brief note or null"
}}

Rules:
- Use standard Indian/international serving sizes when quantity is not specified.
- Multiply macros by quantity (e.g. "2 eggs" = 2x the macros of 1 egg).
- All numbers must be plain integers or decimals — no units like "g" or "kcal".

Meal: {text}"""


def parse_meal_text(text: str) -> dict:
    """Parse a natural-language meal description into macro totals."""
    client = _get_client()
    if client is None:
        logger.warning("GEMINI_API_KEY not set — using mock parser.")
        return _mock_parse(text)

    try:
        interaction = client.interactions.create(
            model="gemini-3.8-flash",
            input=_TEXT_PROMPT.format(text=text),
            store=False,
        )
        raw = interaction.output_text or ""
        data = _extract_json(raw)
        if data:
            items = data.get("items") if isinstance(data, dict) else None
            if not items and isinstance(data, dict) and ("calories" in data or "name" in data):
                items = [data]
            if items:
                result = _sum_items(items)
                result["notes"] = data.get("notes") if isinstance(data, dict) else None
                return result
        raise ValueError(f"No JSON found in model output: {raw[:200]}")

    except Exception as exc:
        logger.error("Gemini text parse failed: %s", exc)
        result = _mock_parse(text)
        result["notes"] = f"AI error — built-in database used. ({exc})"
        return result


# ---------------------------------------------------------------------------
# Photo analyser — Gemini 3.8 Flash (multimodal)
# ---------------------------------------------------------------------------

_PHOTO_PROMPT = """\
You are a nutrition expert analysing a food photograph.
Identify every food and drink item visible, estimate portion sizes, and calculate macros.

Return ONLY a valid JSON object in this exact format:
{
  "items": [
    {"name": "Food Name", "calories": 300, "protein": 25, "carbs": 30, "fats": 8}
  ],
  "notes": "brief description of what you identified"
}

Rules:
- All numbers must be plain integers or decimals — no units.
- Use plate/cutlery as a size reference.
- Account for oils, sauces, and sides."""


def analyze_food_photo(image_path: str) -> dict:
    """Analyse a food photo using Gemini vision and return macro breakdown."""
    client = _get_client()
    if client is None:
        return {
            "calories": 0, "protein": 0, "carbs": 0, "fats": 0,
            "items": [],
            "notes": "Photo analysis requires a GEMINI_API_KEY. Add it to your .env file or Render environment variables.",
        }

    try:
        # Read and base64-encode the image
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Detect MIME type from file extension
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/jpeg"

        interaction = client.interactions.create(
            model="gemini-3.8-flash",
            input=[
                {"type": "text",  "text": _PHOTO_PROMPT},
                {"type": "image", "data": b64, "mime_type": mime_type},
            ],
            store=False,
        )
        raw = interaction.output_text or ""
        data = _extract_json(raw)
        if data:
            items = data.get("items") if isinstance(data, dict) else None
            if not items and isinstance(data, dict) and ("calories" in data or "name" in data):
                items = [data]
            if items:
                result = _sum_items(items)
                result["notes"] = data.get("notes") if isinstance(data, dict) else None
                return result
        raise ValueError(f"No JSON in model output: {raw[:200]}")

    except Exception as exc:
        logger.error("Gemini photo analysis failed: %s", exc)
        return {
            "calories": 0, "protein": 0, "carbs": 0, "fats": 0,
            "items": [],
            "notes": f"Photo analysis error: {exc}. Enter macros manually.",
        }
