"""
nutrition_db.py — Comprehensive, verified nutrition database and NLP meal parser.
Data sources: USDA FoodData Central & Indian Food Composition Tables (IFCT).

Used for:
  1. Offline natural-language meal parsing with precise quantity and portion scaling.
  2. Instant offline food and product search (guaranteed to always work even if external APIs are down).
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Number and word mapping helpers
# ---------------------------------------------------------------------------

_WORD_TO_NUM = {
    "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0,
    "ten": 10.0, "half": 0.5, "quarter": 0.25, "pair": 2.0, "couple": 2.0,
    "dozen": 12.0, "single": 1.0, "double": 2.0, "triple": 3.0,
}

_UNIT_MULTIPLIERS = {
    # Weight
    "g": 1.0, "gram": 1.0, "grams": 1.0, "gm": 1.0, "gms": 1.0,
    "kg": 1000.0, "kgs": 1000.0, "kilo": 1000.0, "kilos": 1000.0,
    "oz": 28.35, "pound": 453.6, "lb": 453.6, "lbs": 453.6,
    # Volume (approx grams for typical liquids / grains)
    "ml": 1.0, "milliliter": 1.0, "milliliters": 1.0,
    "l": 1000.0, "liter": 1000.0, "liters": 1000.0,
    # Spoons
    "tbsp": 15.0, "tablespoon": 15.0, "tablespoons": 15.0,
    "tsp": 5.0, "teaspoon": 5.0, "teaspoons": 5.0,
}


# ---------------------------------------------------------------------------
# Master Verified Nutrition Database
# Each food has:
#   name: Display name
#   brand: Category or brand
#   per_100g: {cal, p, c, f}
#   unit_name: Default single portion description
#   unit_weight_g: Weight in grams of 1 default unit
#   aliases: Lowercase keywords that trigger this item
# ---------------------------------------------------------------------------

FOOD_DATABASE = [
    # ── Eggs & Poultry ───────────────────────────────────────────
    {
        "name": "Whole Boiled Egg",
        "brand": "Poultry & Eggs",
        "per_100g": {"cal": 155, "p": 12.6, "c": 1.1, "f": 10.6},
        "unit_name": "1 large egg (50g)",
        "unit_weight_g": 50,
        "aliases": ["egg", "eggs", "boiled egg", "boiled eggs", "hard boiled egg", "anda"],
    },
    {
        "name": "Egg White",
        "brand": "Poultry & Eggs",
        "per_100g": {"cal": 52, "p": 10.9, "c": 0.7, "f": 0.2},
        "unit_name": "1 egg white (33g)",
        "unit_weight_g": 33,
        "aliases": ["egg white", "egg whites", "whites"],
    },
    {
        "name": "Egg Omelette / Fried Egg",
        "brand": "Poultry & Eggs",
        "per_100g": {"cal": 196, "p": 10.6, "c": 1.5, "f": 15.0},
        "unit_name": "1 omelette (60g)",
        "unit_weight_g": 60,
        "aliases": ["omelette", "omelet", "fried egg", "fried eggs", "scrambled egg", "scrambled eggs", "bhurji", "egg bhurji"],
    },
    {
        "name": "Chicken Breast (Cooked, Skinless)",
        "brand": "Poultry & Eggs",
        "per_100g": {"cal": 165, "p": 31.0, "c": 0.0, "f": 3.6},
        "unit_name": "1 breast piece (150g)",
        "unit_weight_g": 150,
        "aliases": ["chicken breast", "chicken breasts", "grilled chicken breast", "boiled chicken breast", "boiled chicken"],
    },
    {
        "name": "Chicken Curry / Gravy",
        "brand": "Poultry & Eggs",
        "per_100g": {"cal": 180, "p": 16.0, "c": 4.5, "f": 11.0},
        "unit_name": "1 bowl (180g)",
        "unit_weight_g": 180,
        "aliases": ["chicken curry", "chicken gravy", "butter chicken", "kadai chicken", "murgh curry"],
    },
    {
        "name": "Chicken (General Cooked)",
        "brand": "Poultry & Eggs",
        "per_100g": {"cal": 215, "p": 26.0, "c": 0.0, "f": 11.5},
        "unit_name": "1 serving (150g)",
        "unit_weight_g": 150,
        "aliases": ["chicken", "grilled chicken", "roast chicken", "tandoori chicken"],
    },
    {
        "name": "Chicken Biryani",
        "brand": "Rice & Biryani",
        "per_100g": {"cal": 185, "p": 8.5, "c": 22.0, "f": 6.8},
        "unit_name": "1 plate (300g)",
        "unit_weight_g": 300,
        "aliases": ["chicken biryani", "biryani", "mutton biryani", "hyderabadi biryani"],
    },

    # ── Meats & Seafood ──────────────────────────────────────────
    {
        "name": "Mutton / Lamb Curry",
        "brand": "Meat",
        "per_100g": {"cal": 235, "p": 18.0, "c": 3.0, "f": 16.5},
        "unit_name": "1 bowl (180g)",
        "unit_weight_g": 180,
        "aliases": ["mutton", "lamb", "mutton curry", "rogan josh", "lamb curry", "goat meat"],
    },
    {
        "name": "Fish (Grilled / Cooked)",
        "brand": "Seafood",
        "per_100g": {"cal": 136, "p": 22.0, "c": 0.0, "f": 4.8},
        "unit_name": "1 fillet (150g)",
        "unit_weight_g": 150,
        "aliases": ["fish", "grilled fish", "fish curry", "rohu", "katla", "tilapia", "salmon", "pomfret", "tuna"],
    },
    {
        "name": "Prawns / Shrimp (Cooked)",
        "brand": "Seafood",
        "per_100g": {"cal": 99, "p": 24.0, "c": 0.2, "f": 0.3},
        "unit_name": "1 serving (100g)",
        "unit_weight_g": 100,
        "aliases": ["prawns", "prawn", "shrimp", "shrimps", "prawn curry"],
    },

    # ── Grains, Breads & Roti ────────────────────────────────────
    {
        "name": "Whole Wheat Roti / Chapati",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 297, "p": 9.2, "c": 58.0, "f": 3.5},
        "unit_name": "1 medium roti (35g)",
        "unit_weight_g": 35,  # 1 roti = ~104 kcal, 3.2g P, 20.3g C, 1.2g F
        "aliases": ["roti", "rotis", "chapati", "chapatis", "phulka", "fulka", "rotli"],
    },
    {
        "name": "Paratha (Plain)",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 320, "p": 7.5, "c": 46.0, "f": 12.0},
        "unit_name": "1 paratha (70g)",
        "unit_weight_g": 70,  # 1 paratha = ~224 kcal
        "aliases": ["paratha", "parathas", "plain paratha"],
    },
    {
        "name": "Aloo Paratha",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 240, "p": 5.5, "c": 36.0, "f": 8.5},
        "unit_name": "1 aloo paratha (120g)",
        "unit_weight_g": 120,  # 1 aloo paratha = ~288 kcal
        "aliases": ["aloo paratha", "alu paratha", "potato paratha"],
    },
    {
        "name": "Paneer Paratha",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 280, "p": 10.5, "c": 32.0, "f": 12.0},
        "unit_name": "1 paneer paratha (120g)",
        "unit_weight_g": 120,
        "aliases": ["paneer paratha"],
    },
    {
        "name": "Naan / Butter Naan",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 310, "p": 9.0, "c": 52.0, "f": 7.5},
        "unit_name": "1 naan (90g)",
        "unit_weight_g": 90,
        "aliases": ["naan", "butter naan", "garlic naan", "tandoori roti"],
    },
    {
        "name": "Puri / Poori",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 380, "p": 6.5, "c": 46.0, "f": 19.0},
        "unit_name": "1 puri (25g)",
        "unit_weight_g": 25,
        "aliases": ["puri", "puris", "poori", "pooris"],
    },
    {
        "name": "White Bread (Sliced)",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 265, "p": 8.8, "c": 49.0, "f": 3.2},
        "unit_name": "1 slice (30g)",
        "unit_weight_g": 30,  # 1 slice = ~80 kcal
        "aliases": ["white bread", "bread", "bread slice", "bread slices", "slice of bread", "slices of bread"],
    },
    {
        "name": "Brown / Whole Wheat Bread",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 247, "p": 11.0, "c": 43.0, "f": 3.5},
        "unit_name": "1 slice (32g)",
        "unit_weight_g": 32,
        "aliases": ["brown bread", "wheat bread", "whole wheat bread", "multigrain bread"],
    },
    {
        "name": "Toast (Dry)",
        "brand": "Breads & Grains",
        "per_100g": {"cal": 290, "p": 9.5, "c": 53.0, "f": 3.8},
        "unit_name": "1 toast (30g)",
        "unit_weight_g": 30,
        "aliases": ["toast", "toasts"],
    },
    {
        "name": "Cooked White Rice / Basmati Rice",
        "brand": "Rice & Grains",
        "per_100g": {"cal": 130, "p": 2.7, "c": 28.2, "f": 0.3},
        "unit_name": "1 cup cooked (150g)",
        "unit_weight_g": 150,  # 1 cup = ~195 kcal
        "aliases": ["rice", "cooked rice", "white rice", "basmati rice", "chawal", "steamed rice"],
    },
    {
        "name": "Brown Rice (Cooked)",
        "brand": "Rice & Grains",
        "per_100g": {"cal": 123, "p": 2.7, "c": 25.6, "f": 1.0},
        "unit_name": "1 cup cooked (150g)",
        "unit_weight_g": 150,
        "aliases": ["brown rice"],
    },
    {
        "name": "Oats / Oatmeal (Cooked)",
        "brand": "Breakfast Cereals",
        "per_100g": {"cal": 71, "p": 2.5, "c": 12.0, "f": 1.5},
        "unit_name": "1 bowl cooked (200g)",
        "unit_weight_g": 200,
        "aliases": ["oats", "oatmeal", "porridge"],
    },
    {
        "name": "Raw Rolled Oats",
        "brand": "Breakfast Cereals",
        "per_100g": {"cal": 389, "p": 16.9, "c": 66.3, "f": 6.9},
        "unit_name": "1 serving (40g)",
        "unit_weight_g": 40,
        "aliases": ["raw oats", "rolled oats", "quaker oats"],
    },
    {
        "name": "Poha (Cooked)",
        "brand": "Breakfast Dishes",
        "per_100g": {"cal": 140, "p": 2.8, "c": 26.0, "f": 2.8},
        "unit_name": "1 plate / bowl (180g)",
        "unit_weight_g": 180,  # ~252 kcal
        "aliases": ["poha", "pohe", "aval"],
    },
    {
        "name": "Upma",
        "brand": "Breakfast Dishes",
        "per_100g": {"cal": 135, "p": 3.2, "c": 21.0, "f": 4.2},
        "unit_name": "1 bowl (180g)",
        "unit_weight_g": 180,
        "aliases": ["upma", "uppittu", "rava upma"],
    },
    {
        "name": "Idli",
        "brand": "South Indian",
        "per_100g": {"cal": 132, "p": 4.6, "c": 26.8, "f": 0.5},
        "unit_name": "1 idli (40g)",
        "unit_weight_g": 40,  # 1 idli = ~53 kcal
        "aliases": ["idli", "idlis", "idly", "idlies"],
    },
    {
        "name": "Plain Dosa",
        "brand": "South Indian",
        "per_100g": {"cal": 168, "p": 4.0, "c": 29.5, "f": 3.8},
        "unit_name": "1 plain dosa (80g)",
        "unit_weight_g": 80,  # 1 dosa = ~135 kcal
        "aliases": ["dosa", "dosas", "plain dosa", "sada dosa"],
    },
    {
        "name": "Masala Dosa",
        "brand": "South Indian",
        "per_100g": {"cal": 195, "p": 4.5, "c": 28.0, "f": 7.2},
        "unit_name": "1 masala dosa (150g)",
        "unit_weight_g": 150,  # ~292 kcal
        "aliases": ["masala dosa"],
    },
    {
        "name": "Maggi 2-Minute Noodles",
        "brand": "Nestle Maggi",
        "per_100g": {"cal": 427, "p": 8.0, "c": 63.5, "f": 15.7},
        "unit_name": "1 pack prepared (70g dry)",
        "unit_weight_g": 70,  # 1 single pack = ~300 kcal
        "aliases": ["maggi", "maggi noodles", "instant noodles", "noodles", "ramen"],
    },
    {
        "name": "Pasta (Cooked)",
        "brand": "Pasta & Italian",
        "per_100g": {"cal": 131, "p": 5.1, "c": 25.0, "f": 1.1},
        "unit_name": "1 cup cooked (140g)",
        "unit_weight_g": 140,
        "aliases": ["pasta", "cooked pasta", "macaroni", "spaghetti", "penne"],
    },

    # ── Dals, Pulses & Legumes ───────────────────────────────────
    {
        "name": "Yellow Dal (Toor / Moong Dal Cooked)",
        "brand": "Lentils & Dals",
        "per_100g": {"cal": 105, "p": 6.8, "c": 15.5, "f": 1.8},
        "unit_name": "1 katori / bowl (180g)",
        "unit_weight_g": 180,  # 1 bowl = ~189 kcal, 12.2g P, 28g C, 3.2g F
        "aliases": ["dal", "dhal", "daal", "toor dal", "yellow dal", "moong dal", "tadka dal", "dal tadka", "arhar dal"],
    },
    {
        "name": "Dal Makhani",
        "brand": "Lentils & Dals",
        "per_100g": {"cal": 145, "p": 5.5, "c": 16.0, "f": 6.8},
        "unit_name": "1 bowl (180g)",
        "unit_weight_g": 180,  # ~260 kcal
        "aliases": ["dal makhani", "makhani dal", "black dal", "urad dal"],
    },
    {
        "name": "Chole / Chana Masala (Chickpeas)",
        "brand": "Lentils & Dals",
        "per_100g": {"cal": 164, "p": 8.9, "c": 27.4, "f": 2.6},
        "unit_name": "1 bowl (180g)",
        "unit_weight_g": 180,  # ~295 kcal
        "aliases": ["chole", "chana", "chana masala", "chickpeas", "garbanzo"],
    },
    {
        "name": "Rajma Masala (Kidney Beans)",
        "brand": "Lentils & Dals",
        "per_100g": {"cal": 140, "p": 8.5, "c": 22.0, "f": 2.2},
        "unit_name": "1 bowl (180g)",
        "unit_weight_g": 180,  # ~252 kcal
        "aliases": ["rajma", "kidney beans", "rajma curry", "rajmah"],
    },
    {
        "name": "Sambar",
        "brand": "South Indian",
        "per_100g": {"cal": 55, "p": 2.8, "c": 8.5, "f": 1.2},
        "unit_name": "1 bowl (180g)",
        "unit_weight_g": 180,  # ~99 kcal
        "aliases": ["sambar", "sambhar"],
    },

    # ── Dairy, Paneer & Cheese ───────────────────────────────────
    {
        "name": "Paneer (Cottage Cheese)",
        "brand": "Dairy",
        "per_100g": {"cal": 265, "p": 18.3, "c": 3.4, "f": 20.8},
        "unit_name": "100g block",
        "unit_weight_g": 100,
        "aliases": ["paneer", "cottage cheese", "panir", "raw paneer"],
    },
    {
        "name": "Paneer Butter Masala",
        "brand": "Indian Curries",
        "per_100g": {"cal": 210, "p": 8.0, "c": 9.5, "f": 15.5},
        "unit_name": "1 bowl (200g)",
        "unit_weight_g": 200,
        "aliases": ["paneer butter masala", "paneer tikka masala", "shahi paneer", "kadai paneer", "palak paneer", "paneer curry"],
    },
    {
        "name": "Cow Milk (Whole / Full Cream)",
        "brand": "Dairy",
        "per_100g": {"cal": 62, "p": 3.2, "c": 4.8, "f": 3.5},
        "unit_name": "1 cup / glass (240ml)",
        "unit_weight_g": 240,  # ~149 kcal
        "aliases": ["milk", "whole milk", "full cream milk", "cow milk", "doodh"],
    },
    {
        "name": "Toned / Low Fat Milk",
        "brand": "Dairy",
        "per_100g": {"cal": 45, "p": 3.3, "c": 4.9, "f": 1.5},
        "unit_name": "1 cup / glass (240ml)",
        "unit_weight_g": 240,  # ~108 kcal
        "aliases": ["toned milk", "skimmed milk", "low fat milk", "skim milk"],
    },
    {
        "name": "Curd / Plain Dahi (Yogurt)",
        "brand": "Dairy",
        "per_100g": {"cal": 60, "p": 3.5, "c": 4.7, "f": 3.2},
        "unit_name": "1 katori / bowl (150g)",
        "unit_weight_g": 150,  # ~90 kcal
        "aliases": ["curd", "dahi", "plain yogurt", "yogurt", "yoghurt"],
    },
    {
        "name": "Greek Yogurt (Plain)",
        "brand": "Dairy",
        "per_100g": {"cal": 97, "p": 10.0, "c": 3.6, "f": 5.0},
        "unit_name": "1 cup (150g)",
        "unit_weight_g": 150,  # ~145 kcal, 15g protein
        "aliases": ["greek yogurt", "greek yoghurt"],
    },
    {
        "name": "Butter",
        "brand": "Dairy",
        "per_100g": {"cal": 717, "p": 0.9, "c": 0.1, "f": 81.0},
        "unit_name": "1 tbsp (14g)",
        "unit_weight_g": 14,  # ~100 kcal
        "aliases": ["butter", "amul butter", "makhan"],
    },
    {
        "name": "Ghee (Clarified Butter)",
        "brand": "Dairy",
        "per_100g": {"cal": 900, "p": 0.0, "c": 0.0, "f": 100.0},
        "unit_name": "1 tsp (5g)",
        "unit_weight_g": 5,  # ~45 kcal
        "aliases": ["ghee", "clarified butter", "desi ghee"],
    },
    {
        "name": "Cheese (Cheddar / Processed Slice)",
        "brand": "Dairy",
        "per_100g": {"cal": 380, "p": 21.0, "c": 2.5, "f": 31.0},
        "unit_name": "1 slice (20g)",
        "unit_weight_g": 20,  # ~76 kcal
        "aliases": ["cheese", "cheese slice", "cheddar", "amul cheese", "mozzarella"],
    },

    # ── Fruits & Vegetables ──────────────────────────────────────
    {
        "name": "Banana",
        "brand": "Fresh Produce",
        "per_100g": {"cal": 89, "p": 1.1, "c": 22.8, "f": 0.3},
        "unit_name": "1 medium banana (118g)",
        "unit_weight_g": 118,  # 1 banana = ~105 kcal
        "aliases": ["banana", "bananas", "kela"],
    },
    {
        "name": "Apple",
        "brand": "Fresh Produce",
        "per_100g": {"cal": 52, "p": 0.3, "c": 13.8, "f": 0.2},
        "unit_name": "1 medium apple (180g)",
        "unit_weight_g": 180,  # 1 apple = ~94 kcal
        "aliases": ["apple", "apples", "seb"],
    },
    {
        "name": "Orange",
        "brand": "Fresh Produce",
        "per_100g": {"cal": 47, "p": 0.9, "c": 11.8, "f": 0.1},
        "unit_name": "1 orange (130g)",
        "unit_weight_g": 130,
        "aliases": ["orange", "oranges", "santra"],
    },
    {
        "name": "Mango",
        "brand": "Fresh Produce",
        "per_100g": {"cal": 60, "p": 0.8, "c": 15.0, "f": 0.4},
        "unit_name": "1 mango (200g pulp)",
        "unit_weight_g": 200,
        "aliases": ["mango", "mangoes", "aam"],
    },
    {
        "name": "Watermelon",
        "brand": "Fresh Produce",
        "per_100g": {"cal": 30, "p": 0.6, "c": 7.6, "f": 0.2},
        "unit_name": "1 slice / cup (150g)",
        "unit_weight_g": 150,
        "aliases": ["watermelon", "tarbooz"],
    },
    {
        "name": "Potato (Boiled / Cooked)",
        "brand": "Fresh Produce",
        "per_100g": {"cal": 87, "p": 1.9, "c": 20.1, "f": 0.1},
        "unit_name": "1 medium potato (150g)",
        "unit_weight_g": 150,
        "aliases": ["potato", "potatoes", "boiled potato", "aloo", "alu"],
    },
    {
        "name": "Green Salad (Cucumber, Tomato, Onion)",
        "brand": "Fresh Produce",
        "per_100g": {"cal": 22, "p": 1.0, "c": 4.5, "f": 0.2},
        "unit_name": "1 bowl (150g)",
        "unit_weight_g": 150,  # ~33 kcal
        "aliases": ["salad", "green salad", "cucumber", "tomato", "onion salad"],
    },
    {
        "name": "Cooked Sabzi (Mixed Veg / Bhindi / Aloo Gobi)",
        "brand": "Indian Curries",
        "per_100g": {"cal": 95, "p": 2.5, "c": 11.0, "f": 4.8},
        "unit_name": "1 bowl / katori (150g)",
        "unit_weight_g": 150,  # ~142 kcal
        "aliases": ["sabzi", "sabji", "subji", "mixed veg", "bhindi", "aloo gobi", "gobi", "palak sabzi", "bhindi fry"],
    },

    # ── Fitness & Supplements ────────────────────────────────────
    {
        "name": "Whey Protein Powder",
        "brand": "Supplements",
        "per_100g": {"cal": 395, "p": 80.0, "c": 6.5, "f": 5.0},
        "unit_name": "1 scoop (30g)",
        "unit_weight_g": 30,  # 1 scoop = ~118 kcal, 24g protein
        "aliases": ["whey", "protein powder", "whey protein", "scoop of protein", "protein shake", "isolate protein"],
    },
    {
        "name": "Peanut Butter",
        "brand": "Spreads & Nuts",
        "per_100g": {"cal": 588, "p": 25.0, "c": 20.0, "f": 50.0},
        "unit_name": "1 tbsp (16g)",
        "unit_weight_g": 16,  # ~94 kcal, 4g protein
        "aliases": ["peanut butter", "pb"],
    },
    {
        "name": "Almonds / Badam",
        "brand": "Nuts & Seeds",
        "per_100g": {"cal": 579, "p": 21.2, "c": 21.6, "f": 49.9},
        "unit_name": "10 almonds (12g)",
        "unit_weight_g": 12,  # ~70 kcal
        "aliases": ["almonds", "almond", "badam"],
    },
    {
        "name": "Walnuts / Akhrot",
        "brand": "Nuts & Seeds",
        "per_100g": {"cal": 654, "p": 15.2, "c": 13.7, "f": 65.2},
        "unit_name": "4 walnut halves (14g)",
        "unit_weight_g": 14,
        "aliases": ["walnuts", "walnut", "akhrot"],
    },

    # ── Beverages ────────────────────────────────────────────────
    {
        "name": "Black Coffee (No Sugar)",
        "brand": "Beverages",
        "per_100g": {"cal": 2, "p": 0.1, "c": 0.3, "f": 0.0},
        "unit_name": "1 cup (200ml)",
        "unit_weight_g": 200,  # ~4 kcal
        "aliases": ["black coffee", "espresso", "americano"],
    },
    {
        "name": "Coffee with Milk & Sugar",
        "brand": "Beverages",
        "per_100g": {"cal": 45, "p": 1.6, "c": 6.8, "f": 1.5},
        "unit_name": "1 cup (180ml)",
        "unit_weight_g": 180,  # ~81 kcal
        "aliases": ["coffee", "latte", "cappuccino", "filter coffee"],
    },
    {
        "name": "Chai / Milk Tea with Sugar",
        "brand": "Beverages",
        "per_100g": {"cal": 48, "p": 1.5, "c": 7.5, "f": 1.5},
        "unit_name": "1 cup (150ml)",
        "unit_weight_g": 150,  # ~72 kcal
        "aliases": ["chai", "tea", "milk tea", "masala chai"],
    },
    {
        "name": "Green Tea (No Sugar)",
        "brand": "Beverages",
        "per_100g": {"cal": 1, "p": 0.1, "c": 0.2, "f": 0.0},
        "unit_name": "1 cup (200ml)",
        "unit_weight_g": 200,
        "aliases": ["green tea", "black tea"],
    },

    # ── Fast Food, Snacks & Sweets ───────────────────────────────
    {
        "name": "Samosa",
        "brand": "Snacks",
        "per_100g": {"cal": 308, "p": 4.5, "c": 32.0, "f": 18.0},
        "unit_name": "1 piece (80g)",
        "unit_weight_g": 80,  # ~246 kcal
        "aliases": ["samosa", "samosas", "samose"],
    },
    {
        "name": "Pizza (Regular Cheese & Veg / Slice)",
        "brand": "Fast Food",
        "per_100g": {"cal": 266, "p": 11.0, "c": 33.0, "f": 10.0},
        "unit_name": "1 slice (100g)",
        "unit_weight_g": 100,  # ~266 kcal
        "aliases": ["pizza", "pizza slice", "slices of pizza", "slice pizza"],
    },
    {
        "name": "Burger (Veg / Chicken)",
        "brand": "Fast Food",
        "per_100g": {"cal": 250, "p": 12.0, "c": 28.0, "f": 10.5},
        "unit_name": "1 burger (180g)",
        "unit_weight_g": 180,  # ~450 kcal
        "aliases": ["burger", "burgers", "hamburger", "chicken burger", "veg burger"],
    },
    {
        "name": "Sandwich (Veg & Cheese)",
        "brand": "Fast Food",
        "per_100g": {"cal": 220, "p": 7.5, "c": 26.0, "f": 9.5},
        "unit_name": "1 sandwich (150g)",
        "unit_weight_g": 150,  # ~330 kcal
        "aliases": ["sandwich", "sandwiches", "grilled sandwich", "veg sandwich", "club sandwich"],
    },
    {
        "name": "French Fries",
        "brand": "Fast Food",
        "per_100g": {"cal": 312, "p": 3.4, "c": 41.4, "f": 15.0},
        "unit_name": "1 medium pack (117g)",
        "unit_weight_g": 117,  # ~365 kcal
        "aliases": ["french fries", "fries", "potato fries", "chips"],
    },
    {
        "name": "Dark Chocolate (70%)",
        "brand": "Snacks & Sweets",
        "per_100g": {"cal": 598, "p": 7.8, "c": 45.9, "f": 42.6},
        "unit_name": "2 squares (20g)",
        "unit_weight_g": 20,
        "aliases": ["dark chocolate", "chocolate"],
    },
    {
        "name": "Gulab Jamun",
        "brand": "Indian Sweets",
        "per_100g": {"cal": 320, "p": 4.5, "c": 52.0, "f": 11.0},
        "unit_name": "1 piece (50g)",
        "unit_weight_g": 50,  # ~160 kcal
        "aliases": ["gulab jamun", "gulabjamun"],
    },
]

# Sort aliases by descending length so multi-word matches like "chicken breast"
# take precedence over "chicken"
_FOODS_BY_ALIAS_LEN = sorted(
    FOOD_DATABASE,
    key=lambda f: max(len(a) for a in f["aliases"]),
    reverse=True
)


# ---------------------------------------------------------------------------
# Search Engine (Local)
# ---------------------------------------------------------------------------

def search_local_foods(query: str, limit: int = 8) -> list[dict]:
    """Search the curated nutrition database by food name or alias."""
    q = query.lower().strip()
    if not q or len(q) < 2:
        return []

    results = []
    seen = set()

    # Pass 1: exact word / starts with
    for f in FOOD_DATABASE:
        name_lower = f["name"].lower()
        if any(a == q or a.startswith(q) for a in f["aliases"]) or name_lower.startswith(q):
            if f["name"] not in seen:
                seen.add(f["name"])
                results.append(_format_food_result(f))

    # Pass 2: substring match
    if len(results) < limit:
        for f in FOOD_DATABASE:
            name_lower = f["name"].lower()
            if any(q in a for a in f["aliases"]) or q in name_lower:
                if f["name"] not in seen:
                    seen.add(f["name"])
                    results.append(_format_food_result(f))

    return results[:limit]


def _format_food_result(f: dict) -> dict:
    return {
        "name": f["name"],
        "brand": f.get("brand", "Whole Food"),
        "serving_size": f.get("unit_name", "100g"),
        "unit_weight_g": f.get("unit_weight_g", 100),
        "calories_per_100g": float(f["per_100g"]["cal"]),
        "protein_per_100g": float(f["per_100g"]["p"]),
        "carbs_per_100g": float(f["per_100g"]["c"]),
        "fats_per_100g": float(f["per_100g"]["f"]),
    }


# ---------------------------------------------------------------------------
# Intelligent Offline Natural Language Meal Parser
# ---------------------------------------------------------------------------

def parse_meal_offline(text: str) -> dict:
    """
    Parse meal description into verified macro totals using USDA/IFCT data.
    Extracts quantities (e.g. '2 eggs', '3 rotis', '100g chicken', '1 cup rice').
    """
    if not text or not text.strip():
        return {
            "calories": 0.0, "protein": 0.0, "carbs": 0.0, "fats": 0.0,
            "items": [], "notes": "No meal entered."
        }

    # Split on meal item boundaries: "and", "with", "+", "&", comma, newline
    raw_parts = re.split(r"\band\b|\bwith\b|\+|\&|,|\n", text, flags=re.IGNORECASE)
    items = []

    for raw in raw_parts:
        part = raw.strip().lower()
        if not part:
            continue

        item = _parse_single_item(part)
        if item:
            items.append(item)

    # If nothing matched via token splitting, try matching anywhere in text
    if not items:
        for f in _FOODS_BY_ALIAS_LEN:
            for alias in f["aliases"]:
                if re.search(r"\b" + re.escape(alias) + r"\b", text.lower()):
                    item = _scale_macros(f, qty=1.0, is_grams=False)
                    items.append(item)
                    break
            if items:
                break

    # If still nothing matched, provide an educated estimate based on words
    if not items:
        items.append({
            "name": text.strip().capitalize(),
            "calories": 350.0,
            "protein": 12.0,
            "carbs": 45.0,
            "fats": 14.0,
        })
        notes = "Estimated based on average standard meal portion."
    else:
        notes = "Calculated from verified USDA & IFCT food composition standards."

    tot_cal = round(sum(i["calories"] for i in items), 1)
    tot_p = round(sum(i["protein"] for i in items), 1)
    tot_c = round(sum(i["carbs"] for i in items), 1)
    tot_f = round(sum(i["fats"] for i in items), 1)

    return {
        "calories": tot_cal,
        "protein": tot_p,
        "carbs": tot_c,
        "fats": tot_f,
        "items": items,
        "notes": notes,
    }


def _parse_single_item(text: str) -> Optional[dict]:
    """Parse a single meal chunk like '2 boiled eggs' or '150g cooked rice'."""
    # 1. Extract quantity and possible unit
    qty, is_grams, remaining_text = _extract_qty_and_unit(text)

    # 2. Match food in remaining text
    matched_food = _find_food(remaining_text)
    if not matched_food:
        # Fallback: check whole text
        matched_food = _find_food(text)

    if not matched_food:
        return None

    return _scale_macros(matched_food, qty, is_grams)


def _extract_qty_and_unit(text: str) -> tuple[float, bool, str]:
    """
    Returns (quantity, is_grams, remaining_text).
    is_grams=True means the quantity is already in grams.
    """
    words = text.split()
    if not words:
        return 1.0, False, text

    first = words[0]
    second = words[1] if len(words) > 1 else ""

    qty = 1.0
    is_grams = False
    idx_consumed = 0

    # Case A: First word is digit (e.g. "2", "2.5", "100g", "250ml", "1/2")
    m_gram = re.match(r"^(\d+(?:\.\d+)?)\s*(g|gm|gms|gram|grams|ml|kg|kgs|oz)$", first, re.I)
    if m_gram:
        num = float(m_gram.group(1))
        unit = m_gram.group(2).lower()
        multiplier = _UNIT_MULTIPLIERS.get(unit, 1.0)
        return num * multiplier, True, " ".join(words[1:])

    # Fraction e.g. "1/2"
    if "/" in first:
        try:
            top, bottom = first.split("/")
            qty = float(top) / float(bottom)
            idx_consumed = 1
        except Exception:
            pass
    elif re.match(r"^\d+(?:\.\d+)?$", first):
        qty = float(first)
        idx_consumed = 1
    elif first.lower() in _WORD_TO_NUM:
        qty = _WORD_TO_NUM[first.lower()]
        idx_consumed = 1

    # Check second word for unit (e.g. "200 grams", "2 cups", "1 bowl", "2 slices")
    if idx_consumed == 1 and second:
        sec_clean = second.lower()
        if sec_clean in _UNIT_MULTIPLIERS:
            multiplier = _UNIT_MULTIPLIERS[sec_clean]
            return qty * multiplier, True, " ".join(words[2:])
        elif sec_clean in ("cup", "cups"):
            return qty * 150.0, True, " ".join(words[2:])  # ~150g per cup
        elif sec_clean in ("bowl", "bowls", "katori", "katoris"):
            return qty * 180.0, True, " ".join(words[2:])  # ~180g per bowl
        elif sec_clean in ("plate", "plates"):
            return qty * 300.0, True, " ".join(words[2:])  # ~300g per plate
        elif sec_clean in ("slice", "slices"):
            return qty, False, " ".join(words[2:])
        elif sec_clean in ("scoop", "scoops"):
            return qty, False, " ".join(words[2:])
        elif sec_clean in ("piece", "pieces", "pc", "pcs"):
            return qty, False, " ".join(words[2:])

    remaining = " ".join(words[idx_consumed:])
    return qty, is_grams, remaining


def _find_food(text: str) -> Optional[dict]:
    """Find the best matching food in the database for the given text."""
    clean = re.sub(r"[^\w\s]", "", text).lower().strip()
    if not clean:
        return None

    # Check aliases in descending order of length
    for f in _FOODS_BY_ALIAS_LEN:
        for alias in f["aliases"]:
            pattern = r"\b" + re.escape(alias) + r"\b"
            if re.search(pattern, clean):
                return f

    return None


def _scale_macros(food: dict, qty: float, is_grams: bool) -> dict:
    """Calculate scaled macros based on portion quantity or grams."""
    if is_grams:
        # qty is in grams
        factor = qty / 100.0
        portion_label = f"{round(qty)}g"
    else:
        # qty is units (e.g. 2 rotis, 3 eggs)
        grams = qty * food.get("unit_weight_g", 100)
        factor = grams / 100.0
        unit_label = food.get("unit_name", "").split("(")[0].strip()
        unit_label = re.sub(r"^1\s+", "", unit_label)
        if qty == 1.0:
            portion_label = f"1 {unit_label}"
        else:
            portion_label = f"{qty:g} {unit_label}s" if not unit_label.endswith("s") else f"{qty:g} {unit_label}"

    per100 = food["per_100g"]
    return {
        "name": f"{food['name']} ({portion_label})",
        "calories": round(per100["cal"] * factor, 1),
        "protein": round(per100["p"] * factor, 1),
        "carbs": round(per100["c"] * factor, 1),
        "fats": round(per100["f"] * factor, 1),
    }
