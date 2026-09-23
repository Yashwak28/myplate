"""
app.py — Flask application for MyPlate, your personal calorie tracker.

# Force UTF-8 output on Windows so emoji in logs/prints don't crash
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

Routes
------
  GET  /                          — single-page dashboard
  GET  /uploads/<filename>        — serve uploaded food photos

  POST /api/setup_profile         — first-run wizard: save body stats + calculated goals
  GET  /api/profile               — get profile + setup status
  GET  /api/settings              — get current macro goals
  PUT  /api/settings              — update macro goals

  GET  /api/today                 — today's summary, totals, meals, goals
  POST /api/log_meal              — log a meal (text / pre-parsed macros)
  POST /api/analyze_photo         — upload food photo → Ollama LLaVA → macros
  GET  /api/search_food           — proxy Open Food Facts food search
  PUT  /api/meals/<id>            — edit meal macros
  DELETE /api/meals/<id>          — delete a meal
  POST /api/flex_toggle           — toggle today's flex-day

  GET  /api/trends                — last-7-days calorie + protein data

All JSON APIs return { "ok": bool, "data": ..., "error": str|null }.
"""

import logging
import math
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, render_template, request, send_from_directory

import database as db
from ai_parser import analyze_food_photo, parse_meal_text
from food_search import calculate_serving, get_suggestions, search_food

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024   # 20 MB upload limit

# Initialize database schema and tables on startup
db.init_db()

UPLOAD_FOLDER = Path(__file__).parent / "static" / "uploads"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic"}


def _ok(data=None):
    return jsonify({"ok": True, "data": data, "error": None})


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "data": None, "error": msg}), status


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Initialise DB on startup
# ---------------------------------------------------------------------------

with app.app_context():
    db.init_db()


# ---------------------------------------------------------------------------
# TDEE calculation (Mifflin-St Jeor)
# ---------------------------------------------------------------------------

_ACTIVITY_MULT = {
    "sedentary":   1.2,
    "light":       1.375,
    "moderate":    1.55,
    "active":      1.725,
    "very_active": 1.9,
}


def _calculate_goals(age: int, weight_kg: float, height_cm: float,
                     gender: str, activity: str, goal: str) -> dict:
    """Return recommended daily macro goals from TDEE."""
    if gender == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    tdee = bmr * _ACTIVITY_MULT.get(activity, 1.55)

    if goal == "lose":
        calories = tdee * 0.80       # –20% deficit
    elif goal == "gain":
        calories = tdee * 1.10       # +10% surplus
    else:
        calories = tdee

    return {
        "calories": math.ceil(calories),
        "protein":  math.ceil(calories * 0.30 / 4),
        "carbs":    math.ceil(calories * 0.40 / 4),
        "fats":     math.ceil(calories * 0.30 / 9),
    }


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/uploads/<path:filename>")
def serve_upload(filename: str):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ---------------------------------------------------------------------------
# Profile / Setup
# ---------------------------------------------------------------------------

@app.route("/api/profile")
def api_get_profile():
    try:
        user    = db.get_user()
        profile = db.get_profile()
        return _ok({
            "is_setup_complete": bool(user.get("is_setup_complete", 0)),
            "name":   user.get("name", "Friend"),
            "profile": profile,
            "goals": {
                "calories": user.get("calorie_goal", 2000),
                "protein":  user.get("protein_goal", 150),
                "carbs":    user.get("carb_goal",    250),
                "fats":     user.get("fat_goal",      65),
            },
        })
    except Exception as e:
        logger.exception("Error in /api/profile")
        return _err(str(e), 500)


@app.route("/api/setup_profile", methods=["POST"])
def api_setup_profile():
    """
    Called from the first-run wizard.
    Saves body stats and sets computed macro goals.
    """
    body = request.get_json(silent=True) or {}
    try:
        name       = str(body.get("name", "Friend")).strip() or "Friend"
        age        = int(body["age"])
        weight_kg  = float(body["weight_kg"])
        height_cm  = float(body["height_cm"])
        gender     = str(body["gender"])
        activity   = str(body["activity"])
        goal       = str(body["goal"])

        db.save_profile(1, age, weight_kg, height_cm, gender, activity, goal)

        goals = _calculate_goals(age, weight_kg, height_cm, gender, activity, goal)
        db.complete_setup(
            user_id=1,
            name=name,
            calorie_goal=goals["calories"],
            protein_goal=goals["protein"],
            carb_goal=goals["carbs"],
            fat_goal=goals["fats"],
        )
        return _ok({"goals": goals, "name": name})
    except KeyError as e:
        return _err(f"Missing field: {e}")
    except Exception as e:
        logger.exception("Error in /api/setup_profile")
        return _err(str(e), 500)


@app.route("/api/calculate_tdee", methods=["POST"])
def api_calculate_tdee():
    """Live TDEE preview used by the wizard (doesn't save anything)."""
    body = request.get_json(silent=True) or {}
    try:
        goals = _calculate_goals(
            age=int(body["age"]),
            weight_kg=float(body["weight_kg"]),
            height_cm=float(body["height_cm"]),
            gender=str(body["gender"]),
            activity=str(body["activity"]),
            goal=str(body["goal"]),
        )
        return _ok(goals)
    except Exception as e:
        return _err(str(e))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    try:
        user = db.get_user()
        active_key = os.environ.get("GEMINI_API_KEY", "").strip()
        db_key = user.get("gemini_api_key", "").strip() if user else ""
        has_key = bool((active_key and "your" not in active_key.lower()) or (db_key and "your" not in db_key.lower()))
        return _ok({
            "name":            user.get("name", "Friend"),
            "calories":        user.get("calorie_goal", 2000),
            "protein":         user.get("protein_goal", 150),
            "carbs":           user.get("carb_goal",    250),
            "fats":            user.get("fat_goal",      65),
            "has_gemini_key":  has_key,
            "gemini_api_key":  db_key or (active_key if "your" not in active_key.lower() else ""),
        })
    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/settings", methods=["PUT"])
def api_update_settings():
    body = request.get_json(silent=True) or {}
    try:
        key = body.get("gemini_api_key")
        if key is not None:
            clean_key = str(key).strip()
            if "your" not in clean_key.lower() and "api-key" not in clean_key.lower():
                os.environ["GEMINI_API_KEY"] = clean_key
            else:
                clean_key = ""
        else:
            clean_key = None

        db.update_user_goals(
            user_id=1,
            calorie_goal=float(body.get("calories", 2000)),
            protein_goal=float(body.get("protein",  150)),
            carb_goal=float(body.get("carbs",       250)),
            fat_goal=float(body.get("fats",          65)),
            gemini_api_key=clean_key,
        )
        return _ok({"message": "Settings updated."})
    except Exception as e:
        return _err(str(e), 500)


# ---------------------------------------------------------------------------
# Today's dashboard
# ---------------------------------------------------------------------------

@app.route("/api/today")
def api_today():
    try:
        user    = db.get_user()
        summary = db.get_or_create_daily_summary()
        totals  = db.get_daily_totals(summary["id"])
        meals   = db.get_meals_for_day(summary["id"])
        return _ok({
            "summary":    summary,
            "totals":     totals,
            "meals":      meals,
            "is_flex_day": bool(summary["is_flex_day"]),
            "goals": {
                "calories": user.get("calorie_goal", 2000),
                "protein":  user.get("protein_goal", 150),
                "carbs":    user.get("carb_goal",    250),
                "fats":     user.get("fat_goal",      65),
            },
        })
    except Exception as e:
        logger.exception("Error in /api/today")
        return _err(str(e), 500)


# ---------------------------------------------------------------------------
# Log a meal (text or pre-parsed)
# ---------------------------------------------------------------------------

@app.route("/api/log_meal", methods=["POST"])
def api_log_meal():
    """
    Body (JSON):
      text           — what the user ate (required)
      calories       — if provided, skip AI (use pre-parsed macros)
      protein        — same
      carbs          — same
      fats           — same
      source         — 'text' | 'photo' | 'search'  (default: 'text')
      photo_filename — saved photo name (optional)
    """
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return _err("text is required")

    source         = body.get("source", "text")
    photo_filename = body.get("photo_filename")

    try:
        if "calories" in body:
            # macros already computed (from photo analysis or food search)
            macros = {
                "calories": float(body.get("calories", 0)),
                "protein":  float(body.get("protein",  0)),
                "carbs":    float(body.get("carbs",    0)),
                "fats":     float(body.get("fats",     0)),
            }
        else:
            macros = parse_meal_text(text)

        summary = db.get_or_create_daily_summary()
        meal    = db.add_meal_entry(
            summary_id=summary["id"],
            raw_text=text,
            calories=macros["calories"],
            protein=macros["protein"],
            carbs=macros["carbs"],
            fats=macros["fats"],
            source=source,
            photo_filename=photo_filename,
        )
        totals = db.get_daily_totals(summary["id"])
        return _ok({"meal": meal, "totals": totals})
    except Exception as e:
        logger.exception("Error in /api/log_meal")
        return _err(str(e), 500)


# ---------------------------------------------------------------------------
# Photo analysis
# ---------------------------------------------------------------------------

@app.route("/api/analyze_photo", methods=["POST"])
def api_analyze_photo():
    """
    Multipart upload: field name = 'photo'.
    Saves the file, runs LLaVA, returns macro estimates.
    The file stays saved so the meal card can show a thumbnail.
    """
    if "photo" not in request.files:
        return _err("No photo uploaded")

    file = request.files["photo"]
    if not file.filename or not _allowed(file.filename):
        return _err("Invalid file. Supported formats: JPG, PNG, WebP, GIF")

    ext      = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    path     = UPLOAD_FOLDER / filename

    try:
        file.save(str(path))
    except Exception as e:
        return _err(f"Could not save file: {e}", 500)

    macros = analyze_food_photo(str(path))
    return _ok({
        "macros":         macros,
        "photo_filename": filename,
    })


# ---------------------------------------------------------------------------
# Food search (Open Food Facts)
# ---------------------------------------------------------------------------

@app.route("/api/search_food")
def api_search_food():
    query = (request.args.get("q") or "").strip()
    try:
        if not query or len(query) < 2:
            return _ok(get_suggestions())
        results = search_food(query)
        return _ok(results)
    except Exception as e:
        logger.exception("Error in /api/search_food")
        return _err(str(e), 500)


@app.route("/api/serving_macros", methods=["POST"])
def api_serving_macros():
    """Calculate macros for a custom serving size (in grams)."""
    body = request.get_json(silent=True) or {}
    try:
        grams = float(body.get("grams", 100))
        food  = body.get("food", {})
        return _ok(calculate_serving(food, grams))
    except Exception as e:
        return _err(str(e))


# ---------------------------------------------------------------------------
# Meal CRUD
# ---------------------------------------------------------------------------

@app.route("/api/meals/<int:meal_id>", methods=["PUT"])
def api_edit_meal(meal_id: int):
    body = request.get_json(silent=True) or {}
    try:
        meal = db.update_meal_entry(
            meal_id=meal_id,
            calories=float(body.get("calories", 0)),
            protein=float(body.get("protein",   0)),
            carbs=float(body.get("carbs",        0)),
            fats=float(body.get("fats",          0)),
        )
        if not meal:
            return _err("Meal not found", 404)
        summary = db.get_or_create_daily_summary()
        totals  = db.get_daily_totals(summary["id"])
        return _ok({"meal": meal, "totals": totals})
    except Exception as e:
        logger.exception("Error in PUT /api/meals/%s", meal_id)
        return _err(str(e), 500)


@app.route("/api/meals/<int:meal_id>", methods=["DELETE"])
def api_delete_meal(meal_id: int):
    try:
        if not db.delete_meal_entry(meal_id):
            return _err("Meal not found", 404)
        summary = db.get_or_create_daily_summary()
        totals  = db.get_daily_totals(summary["id"])
        return _ok({"totals": totals})
    except Exception as e:
        logger.exception("Error in DELETE /api/meals/%s", meal_id)
        return _err(str(e), 500)


# ---------------------------------------------------------------------------
# Flex day
# ---------------------------------------------------------------------------

@app.route("/api/flex_toggle", methods=["POST"])
def api_flex_toggle():
    try:
        summary = db.get_or_create_daily_summary()
        new_val = db.toggle_flex_day(summary["id"])
        return _ok({"is_flex_day": new_val})
    except Exception as e:
        return _err(str(e), 500)


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------

@app.route("/api/trends")
def api_trends():
    try:
        return _ok(db.get_weekly_trends())
    except Exception as e:
        return _err(str(e), 500)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  [MyPlate]  Running at  http://localhost:{port}")
    print(f"  On your phone/tablet: http://<your-pc-ip>:{port}\n")
    app.run(debug=True, host="0.0.0.0", port=port)
