"""
food_search.py — Open Food Facts integration.

Completely free, no API key, no sign-up.
Returns per-100g nutritional data + serving size for packaged foods.

Usage:
    results = search_food("Maggi Noodles")
    # [{ name, brand, calories_per_100g, protein, carbs, fats, serving_size }, ...]
"""

import logging
import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}"

# Mandatory User-Agent per Open Food Facts API policy
_HEADERS = {"User-Agent": "MyPlate-PersonalTracker/1.0 (localhost)"}

_FIELDS = "product_name,brands,serving_size,nutriments"


def search_food(query: str, page_size: int = 8) -> list[dict]:
    """
    Search Open Food Facts by food/product name.
    Returns a list of nutrition dicts (per-100g values).
    """
    if not query or len(query) < 2:
        return []

    params = {
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": page_size,
        "fields": _FIELDS,
        "sort_by": "unique_scans_n",   # most-scanned (most popular) first
    }

    try:
        resp = requests.get(
            _SEARCH_URL, params=params, headers=_HEADERS, timeout=8
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("Open Food Facts search failed: %s", exc)
        return []

    results = []
    for product in data.get("products", []):
        name = (product.get("product_name") or "").strip()
        if not name:
            continue

        n = product.get("nutriments", {})
        cal = n.get("energy-kcal_100g") or n.get("energy_100g", 0)
        if cal and n.get("energy_unit", "kcal") != "kcal":
            cal = cal / 4.184   # kJ → kcal

        results.append({
            "name":              name,
            "brand":             (product.get("brands") or "").split(",")[0].strip(),
            "serving_size":      product.get("serving_size") or "100g",
            "calories_per_100g": round(float(cal or 0), 1),
            "protein_per_100g":  round(float(n.get("proteins_100g", 0) or 0), 1),
            "carbs_per_100g":    round(float(n.get("carbohydrates_100g", 0) or 0), 1),
            "fats_per_100g":     round(float(n.get("fat_100g", 0) or 0), 1),
        })

    return results


def calculate_serving(food: dict, grams: float) -> dict:
    """Scale per-100g values to an actual serving in grams."""
    factor = grams / 100
    return {
        "calories": round(food["calories_per_100g"] * factor, 1),
        "protein":  round(food["protein_per_100g"]  * factor, 1),
        "carbs":    round(food["carbs_per_100g"]    * factor, 1),
        "fats":     round(food["fats_per_100g"]     * factor, 1),
    }
