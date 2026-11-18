"""Parsers for the public food databases.

Fixtures reproduce the *shape* of each API's documented responses (field names,
nesting, units). Nutrient values in them are copied from the examples in the
providers' own documentation, not invented for the test.
"""
import pytest

from app import foods as F

# Open Food Facts API v2 tutorial example (Nutella, code 3017624010701)
OFF_PRODUCT = {
    "code": "3017624010701",
    "product_name": "Nutella",
    "brands": "Ferrero,Nutella",
    "serving_size": "15 g",
    "serving_quantity": 15,
    "nutriments": {"energy-kcal_100g": 539, "proteins_100g": 6.3, "carbohydrates_100g": 57.5, "fat_100g": 30.9,
                   "sugars_100g": 56.3, "sodium_100g": 0.0428},
}

# FoodData Central /foods/search example (cheddar, SR Legacy) — documented nutrient ids
USDA_FOOD = {
    "fdcId": 170899, "description": "CHEESE,CHEDDAR", "dataType": "SR Legacy",
    "foodNutrients": [
        {"nutrientId": 1003, "nutrientName": "Protein", "unitName": "G", "value": 22.9},
        {"nutrientId": 1004, "nutrientName": "Total lipid (fat)", "unitName": "G", "value": 33.3},
        {"nutrientId": 1005, "nutrientName": "Carbohydrate, by difference", "unitName": "G", "value": 3.09},
        {"nutrientId": 1008, "nutrientName": "Energy", "unitName": "KCAL", "value": 403},
        {"nutrientId": 1062, "nutrientName": "Energy", "unitName": "kJ", "value": 1690},
        {"nutrientId": 1093, "nutrientName": "Sodium, Na", "unitName": "MG", "value": 653},
    ],
}


def test_off_product_normalised_per_100g():
    f = F.parse_off_product(OFF_PRODUCT)
    assert (f.name, f.brand, f.kcal, f.protein, f.carbs, f.fat) == ("Nutella", "Ferrero", 539, 6.3, 57.5, 30.9)
    assert f.sodium_mg == pytest.approx(42.8)
    assert f.servings == [{"label": "15 g", "grams": 15.0}]
    assert f.complete


def test_off_energy_from_kj_when_kcal_missing():
    p = {**OFF_PRODUCT, "nutriments": {"energy-kj_100g": 2255, "proteins_100g": 6.3, "carbohydrates_100g": 57.5,
                                       "fat_100g": 30.9}}
    assert F.parse_off_product(p).kcal == pytest.approx(539, abs=1)


def test_off_missing_values_are_never_guessed():
    p = {"code": "123456789", "product_name": "Mystery bar", "nutriments": {"energy-kcal_100g": 400}}
    f = F.parse_off_product(p)
    assert f.protein is None and not f.complete


def test_usda_uses_kcal_not_kj_and_title_cases():
    f = F.parse_usda_food(USDA_FOOD)
    assert f.kcal == 403 and f.protein == 22.9 and f.fat == 33.3 and f.carbs == 3.09 and f.sodium_mg == 653
    assert f.name == "Cheese,cheddar"


def test_usda_foundation_energy_fallback():
    food = {"fdcId": 1, "description": "Apple", "foodNutrients": [
        {"nutrientId": 2047, "unitName": "KCAL", "value": 61}, {"nutrientId": 1003, "unitName": "G", "value": 0.2},
        {"nutrientId": 1004, "unitName": "G", "value": 0.2}, {"nutrientId": 1005, "unitName": "G", "value": 14.8}]}
    assert F.parse_usda_food(food).kcal == 61


def test_cnf_nutrients_and_servings():
    rows = [{"food_code": 4, "nutrient_value": 52, "nutrient_name_id": 208},
            {"food_code": 4, "nutrient_value": 0.26, "nutrient_name_id": 203},
            {"food_code": 4, "nutrient_value": 13.81, "nutrient_name_id": 205},
            {"food_code": 4, "nutrient_value": 0.17, "nutrient_name_id": 204},
            {"food_code": 4, "nutrient_value": 1, "nutrient_name_id": 306}]
    servings = [{"food_code": 4, "measure_name": "1 pomme moyenne", "conversion_factor_value": 1.82}]
    f = F.parse_cnf_nutrients(4, "Pomme, crue", rows, servings)
    assert (f.kcal, f.protein, f.carbs, f.fat) == (52, 0.26, 13.81, 0.17)
    assert f.servings == [{"label": "1 pomme moyenne", "grams": 182.0}]


def test_cnf_search_is_accent_insensitive():
    index = [(1, "Poulet, poitrine, rôti", "Chicken, breast, roasted"), (2, "Riz blanc, cuit", "Rice, white, cooked"),
             (3, "Poulet, cuisse, rôtie, avec peau", "Chicken, thigh")]
    assert [c for c, _ in F.cnf_match(index, "poulet roti")] == [1, 3]
    assert [c for c, _ in F.cnf_match(index, "rice cooked")] == [2]


def test_barcode_validation():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(F.off_barcode(None, "abc"))


def test_rate_limiter():
    rl = F.RateLimiter(2)
    assert rl.allow() and rl.allow() and not rl.allow()
