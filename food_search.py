"""
food_search.py — Reliable food and product search with Open Food Facts + Gemini AI Fallback.

1. Tries Open Food Facts API first.
2. If Open Food Facts is down (e.g. 503 error), rate-limited, or returns 0 items:
   Seamlessly falls back to Google Gemini 3.8 Flash for instant per-100g nutritional facts.
3. Also checks the local food database for common staples.
"""

import logging
import requests
from ai_parser import _get_client, _extract_json, _clean_num, _MOCK_DB

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
_HEADERS = {"User-Agent": "MyPlate-Tracker/2.0 (contact@myplate.app)"}
_FIELDS = "product_name,brands,serving_size,nutriments"


def _search_openfoodfacts(query: str, page_size: int = 6) -> list[dict]:
    params = {
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": page_size,
        "fields": _FIELDS,
        "sort_by": "unique_scans_n",
    }
    resp = requests.get(_SEARCH_URL, params=params, headers=_HEADERS, timeout=4)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for product in data.get("products", []):
        name = (product.get("product_name") or "").strip()
        if not name:
            continue
        n = product.get("nutriments", {})
        cal = n.get("energy-kcal_100g") or n.get("energy_100g", 0)
        if cal and n.get("energy_unit", "kcal") != "kcal":
            cal = cal / 4.184

        results.append({
            "name": name,
            "brand": (product.get("brands") or "").split(",")[0].strip(),
            "serving_size": product.get("serving_size") or "100g",
            "calories_per_100g": round(float(cal or 0), 1),
            "protein_per_100g": round(float(n.get("proteins_100g", 0) or 0), 1),
            "carbs_per_100g": round(float(n.get("carbohydrates_100g", 0) or 0), 1),
            "fats_per_100g": round(float(n.get("fat_100g", 0) or 0), 1),
        })
    return results


def _search_gemini_fallback(query: str) -> list[dict]:
    client = _get_client()
    if not client:
        return []

    prompt = f"""You are a nutrition database. Provide standard nutritional info per 100g for food/product matching: "{query}".
Return ONLY a valid JSON list of up to 4 popular matching items or brand variations.
Format:
[
  {{
    "name": "Specific Food Name",
    "brand": "Brand or Category",
    "calories_per_100g": 120,
    "protein_per_100g": 5,
    "carbs_per_100g": 20,
    "fats_per_100g": 2,
    "serving_size": "100g"
  }}
]
All numbers must be plain numbers per 100g (no units). Return only JSON."""

    try:
        interaction = client.interactions.create(
            model="gemini-3.8-flash",
            input=prompt,
            store=False
        )
        raw = interaction.output_text or ""
        data = _extract_json(raw)
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("items") or ([data] if "name" in data else [])

        results = []
        for i in items:
            if not isinstance(i, dict) or not i.get("name"):
                continue
            results.append({
                "name": str(i.get("name", "")),
                "brand": str(i.get("brand") or "Verified Food"),
                "serving_size": str(i.get("serving_size") or "100g"),
                "calories_per_100g": _clean_num(i.get("calories_per_100g", 0)),
                "protein_per_100g": _clean_num(i.get("protein_per_100g", 0)),
                "carbs_per_100g": _clean_num(i.get("carbs_per_100g", 0)),
                "fats_per_100g": _clean_num(i.get("fats_per_100g", 0)),
            })
        return results
    except Exception as exc:
        logger.error("Gemini food search failed: %s", exc)
        return []


def _search_local_db(query: str) -> list[dict]:
    q = query.lower().strip()
    matches = []
    for k, v in _MOCK_DB.items():
        if q in k or k in q:
            matches.append({
                "name": k.capitalize(),
                "brand": "Whole Food",
                "serving_size": "100g",
                "calories_per_100g": v["calories"],
                "protein_per_100g": v["protein"],
                "carbs_per_100g": v["carbs"],
                "fats_per_100g": v["fats"],
            })
    return matches


def search_food(query: str, page_size: int = 6) -> list[dict]:
    if not query or len(query.strip()) < 2:
        return []

    # 1. Try Open Food Facts
    try:
        results = _search_openfoodfacts(query, page_size)
        if results:
            return results
    except Exception as exc:
        logger.warning("Open Food Facts unavailable (%s) — falling back to Gemini AI.", exc)

    # 2. Try Gemini AI Nutrition Database
    gemini_results = _search_gemini_fallback(query)
    if gemini_results:
        return gemini_results

    # 3. Try Local Common Foods Database
    return _search_local_db(query)


def calculate_serving(food: dict, grams: float) -> dict:
    factor = grams / 100
    return {
        "calories": round(food.get("calories_per_100g", 0) * factor, 1),
        "protein": round(food.get("protein_per_100g", 0) * factor, 1),
        "carbs": round(food.get("carbs_per_100g", 0) * factor, 1),
        "fats": round(food.get("fats_per_100g", 0) * factor, 1),
    }
