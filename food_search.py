"""
food_search.py — Multi-tiered food and product search engine.

Tier 1: Instant local verified database (USDA & IFCT - 100% reliable, zero network latency).
Tier 2: Google Gemini AI Nutrition Search (for custom, niche, or restaurant items).
Tier 3: Open Food Facts API (for packaged branded barcodes when available).
"""

import logging
import requests
from ai_parser import _get_client, _extract_json, _clean_num
import nutrition_db

logger = logging.getLogger(__name__)

_OPENFOODFACTS_URL = "https://world.openfoodfacts.org/cgi/search.pl"
_HEADERS = {"User-Agent": "MyPlate-Tracker/2.0 (contact@myplate.app)"}
_FIELDS = "product_name,brands,serving_size,nutriments"


def get_suggestions(limit: int = 12) -> list[dict]:
    """Return top popular foods and product suggestions."""
    return nutrition_db.get_popular_suggestions(limit=limit)


def search_food(query: str, page_size: int = 10) -> list[dict]:
    """Search for food items with reliable multi-tier fallback."""
    q = (query or "").strip()
    if len(q) < 2:
        return get_suggestions(limit=page_size)

    results = []
    seen_names = set()

    # Tier 1: Local verified nutrition database (always works, instant)
    try:
        local_items = nutrition_db.search_local_foods(q, limit=page_size)
        for item in local_items:
            name_key = item["name"].lower().strip()
            if name_key not in seen_names:
                seen_names.add(name_key)
                results.append(item)
    except Exception as exc:
        logger.warning("Local food search error: %s", exc)

    # Tier 2: Google Gemini AI (if key is configured and we want more variations)
    client = _get_client()
    if client is not None and len(results) < 3:
        try:
            gemini_items = _search_gemini_fallback(q)
            for item in gemini_items:
                name_key = item["name"].lower().strip()
                if name_key not in seen_names:
                    seen_names.add(name_key)
                    results.append(item)
        except Exception as exc:
            logger.debug("Gemini food search skipped: %s", exc)

    # Tier 3: Open Food Facts (for packaged products if online)
    if len(results) < page_size:
        try:
            off_items = _search_openfoodfacts(q, page_size=page_size)
            for item in off_items:
                name_key = item["name"].lower().strip()
                if name_key not in seen_names:
                    seen_names.add(name_key)
                    results.append(item)
        except Exception as exc:
            logger.debug("Open Food Facts unavailable: %s", exc)

    return results[:page_size]


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
    resp = requests.get(_OPENFOODFACTS_URL, params=params, headers=_HEADERS, timeout=3)
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
            "brand": (product.get("brands") or "").split(",")[0].strip() or "Packaged Food",
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

    prompt = f"""You are a nutrition database. Provide standard nutritional info per 100g for food matching: "{query}".
Return ONLY a valid JSON list of up to 3 popular items:
[
  {{
    "name": "Food Name",
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
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw = response.text or ""
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
        logger.debug("Gemini food search failed: %s", exc)
        return []


def calculate_serving(food: dict, grams: float) -> dict:
    factor = grams / 100.0
    return {
        "calories": round(food.get("calories_per_100g", 0) * factor, 1),
        "protein": round(food.get("protein_per_100g", 0) * factor, 1),
        "carbs": round(food.get("carbs_per_100g", 0) * factor, 1),
        "fats": round(food.get("fats_per_100g", 0) * factor, 1),
    }
