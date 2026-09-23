"""
ai_parser.py — Multimodal food parser using Google Gemini 3.8 Flash with USDA/IFCT fallback.

- If GEMINI_API_KEY is available: uses Google Gemini 3.8 Flash for AI text & photo analysis.
- If GEMINI_API_KEY is not set or fails: seamlessly falls back to nutrition_db (USDA & IFCT verified data).
"""

import base64
import io
import json
import logging
import os
import re

import database as db
import nutrition_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gemini client management
# ---------------------------------------------------------------------------

_gemini_client = None


def _get_api_key() -> str:
    """Retrieve API key from environment or database, strictly validating format."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or "your" in key.lower() or "api-key" in key.lower() or "here" in key.lower():
        try:
            key = db.get_gemini_api_key().strip()
        except Exception:
            key = ""
    if not key or "your" in key.lower() or "api-key" in key.lower() or "here" in key.lower():
        return ""
    # Gemini API keys created in Google AI Studio start with 'AIzaSy' and are 39 chars long
    if not key.startswith("AIzaSy") or len(key) < 30:
        logger.warning("Rejecting invalid Gemini key format (must start with AIzaSy). Falling back to offline DB.")
        return ""
    return key


def _get_client():
    global _gemini_client
    api_key = _get_api_key()
    if not api_key:
        _gemini_client = None
        return None
    if _gemini_client is not None and getattr(_gemini_client, "_active_key", None) == api_key:
        return _gemini_client
    try:
        from google import genai
        # 15s timeout prevents worker hangs on bad network or rate limits
        _gemini_client = genai.Client(api_key=api_key, http_options={"timeout": 15000})
        _gemini_client._active_key = api_key
        return _gemini_client
    except Exception as exc:
        logger.error("Failed to initialise Gemini client: %s", exc)
        _gemini_client = None
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


def _extract_json(text: str) -> dict | list | None:
    """Robustly extract the first JSON object or list from model output."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, (dict, list)):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Text parser
# ---------------------------------------------------------------------------

_TEXT_PROMPT = """\
You are an expert clinical nutritionist with access to USDA FoodData Central and Indian Food Composition Tables.
Parse the meal description into accurate, realistic macronutrients.
Account for quantities (e.g. "2 eggs" = 2x macros, "200g chicken" = 200g macros).

Return ONLY a valid JSON object in this format:
{{
  "items": [
    {{"name": "Food Name (Portion)", "calories": 150, "protein": 12, "carbs": 15, "fats": 5}}
  ],
  "notes": "brief nutritional summary"
}}

Rules:
- All numbers must be plain integers or decimals (no units in numbers).
- Use accurate scientific macronutrient values per portion.

Meal: {text}"""


def parse_meal_text(text: str) -> dict:
    """Parse a natural-language meal description into verified macro totals."""
    client = _get_client()
    if client is None:
        logger.info("Using verified offline nutrition database for meal parsing.")
        return nutrition_db.parse_meal_offline(text)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=_TEXT_PROMPT.format(text=text),
        )
        raw = response.text or ""
        data = _extract_json(raw)
        if data:
            items = data.get("items") if isinstance(data, dict) else None
            if not items and isinstance(data, dict) and ("calories" in data or "name" in data):
                items = [data]
            if items:
                result = _sum_items(items)
                result["notes"] = data.get("notes") if isinstance(data, dict) else None
                return result
        raise ValueError(f"No valid JSON in model output: {raw[:200]}")

    except Exception as exc:
        logger.warning("Gemini text parse failed (%s) — falling back to verified database.", exc)
        result = nutrition_db.parse_meal_offline(text)
        result["notes"] = "Calculated from verified USDA & IFCT food composition standards."
        return result


# ---------------------------------------------------------------------------
# Photo analyser — Gemini 2.5 Flash (multimodal)
# ---------------------------------------------------------------------------

_PHOTO_PROMPT = """\
You are an expert nutritionist analysing a food photograph.
Identify every food and drink item visible, estimate realistic portion sizes, and calculate accurate macros based on USDA standards.

Return ONLY a valid JSON object in this exact format:
{
  "items": [
    {"name": "Food Name (Estimated Portion)", "calories": 300, "protein": 25, "carbs": 30, "fats": 8}
  ],
  "notes": "brief description of identified foods and portion estimates"
}

Rules:
- All numbers must be plain integers or decimals (no units like 'g' or 'kcal').
- Use plate/bowl/cutlery as scale references for realistic portion estimation.
- Account for cooking oils, sauces, dressings, and sides."""


def analyze_food_photo(image_path: str) -> dict:
    """Analyse a food photo using Gemini vision and return macro breakdown."""
    client = _get_client()
    if client is None:
        return {
            "calories": 0, "protein": 0, "carbs": 0, "fats": 0,
            "items": [],
            "notes": "Photo scanning requires a free Gemini API key (starts with AIzaSy). Enter your key in Settings ⚙️ to enable instant AI recognition, or type your meal in the Type Meal tab.",
        }

    try:
        from PIL import Image
        from google.genai import types

        # Optimize image size for faster mobile uploads and prompt transmission
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            image_bytes = buf.getvalue()

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                _PHOTO_PROMPT,
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            ],
        )
        raw = response.text or ""
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
            "notes": f"Photo analysis notice: {exc}. Please verify your Gemini API key in Settings ⚙️ (starts with AIzaSy) or enter meal in Type Meal tab.",
        }
