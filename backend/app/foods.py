"""
Public food databases.

* Canadian Nutrient File (Health Canada) — generic foods, French + English, lab data.
  API: https://produits-sante.canada.ca/api/documentation/cnf-documentation-en.html
  Licence: Open Government Licence – Canada.
* Open Food Facts — packaged products, barcodes. Full-text search uses the v1
  ``cgi/search.pl`` endpoint (v2 has no full-text search) and is limited to
  10 requests/minute, so results are cached and rate-limited here.
  Licence: ODbL — attribution shown in the UI.
* USDA FoodData Central — generic + branded US foods. Needs a free API key
  (``USDA_API_KEY``; ``DEMO_KEY`` works at low volume).

All sources are normalised to nutrients **per 100 g**. Missing values stay
``None`` — they are never guessed — and the food is flagged incomplete.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx

USDA_KEY = os.getenv("USDA_API_KEY", "DEMO_KEY")
CONTACT = os.getenv("OFF_CONTACT", "taper-app")
USER_AGENT = f"Taper/1.0 ({CONTACT})"
TIMEOUT = httpx.Timeout(8.0, connect=4.0)

CNF_BASE = "https://food-nutrition.canada.ca/api/canadian-nutrient-file"
OFF_BASE = "https://world.openfoodfacts.org"
USDA_BASE = "https://api.nal.usda.gov/fdc/v1"

# CNF nutrient_name_id → field (ids per the CNF nutrient dictionary)
CNF_NUTRIENTS = {208: "kcal", 203: "protein", 205: "carbs", 204: "fat", 291: "fiber", 269: "sugar", 307: "sodium_mg"}
# FoodData Central nutrient ids
USDA_NUTRIENTS = {1008: "kcal", 1003: "protein", 1005: "carbs", 1004: "fat", 1079: "fiber", 2000: "sugar",
                  1093: "sodium_mg"}
USDA_ENERGY_FALLBACK = (2047, 2048)   # Atwater general / specific, used by Foundation foods


@dataclass
class FoodData:
    source: str
    source_id: str
    name: str
    brand: Optional[str] = None
    barcode: Optional[str] = None
    kcal: Optional[float] = None
    protein: Optional[float] = None
    carbs: Optional[float] = None
    fat: Optional[float] = None
    fiber: Optional[float] = None
    sugar: Optional[float] = None
    sodium_mg: Optional[float] = None
    servings: list = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return None not in (self.kcal, self.protein, self.carbs, self.fat)

    def to_dict(self) -> dict:
        return {**asdict(self), "complete": self.complete}


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 4) if f >= 0 else None


def fold(text: str) -> str:
    """Lower-case and strip accents so 'poulet rôti' matches 'Poulet, roti'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


# ------------------------------------------------------------------ Open Food Facts
OFF_FIELDS = "code,product_name,product_name_fr,product_name_en,brands,serving_size,serving_quantity,nutriments"


def parse_off_product(p: dict) -> Optional[FoodData]:
    name = p.get("product_name_fr") or p.get("product_name") or p.get("product_name_en")
    code = p.get("code")
    if not name or not code:
        return None
    n = p.get("nutriments") or {}
    kcal = _num(n.get("energy-kcal_100g"))
    if kcal is None and _num(n.get("energy-kj_100g")) is not None:
        kcal = round(_num(n.get("energy-kj_100g")) / 4.184, 1)
    sodium = _num(n.get("sodium_100g"))
    servings = []
    q = _num(p.get("serving_quantity"))
    if q:
        servings.append({"label": p.get("serving_size") or f"{q:g} g", "grams": q})
    return FoodData("off", str(code), name.strip(), (p.get("brands") or "").split(",")[0].strip() or None, str(code),
                    kcal, _num(n.get("proteins_100g")), _num(n.get("carbohydrates_100g")), _num(n.get("fat_100g")),
                    _num(n.get("fiber_100g")), _num(n.get("sugars_100g")),
                    round(sodium * 1000, 1) if sodium is not None else None, servings)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.per_minute:
            return False
        self.calls.append(now)
        return True


_off_limiter = RateLimiter(8)               # OFF asks for ≤ 10 searches/minute
_cache: dict[tuple, tuple[float, list]] = {}
CACHE_TTL = 24 * 3600


async def off_search(client: httpx.AsyncClient, q: str, limit: int = 15) -> list[FoodData]:
    key = ("off", fold(q))
    if key in _cache and time.time() - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    if not _off_limiter.allow():
        raise RuntimeError("Open Food Facts search limit reached — try again in a minute")
    r = await client.get(f"{OFF_BASE}/cgi/search.pl", params={
        "search_terms": q, "search_simple": 1, "action": "process", "json": 1, "page_size": limit,
        "fields": OFF_FIELDS, "lc": "fr"})
    r.raise_for_status()
    out = [f for f in (parse_off_product(p) for p in r.json().get("products", [])) if f]
    _cache[key] = (time.time(), out)
    return out


async def off_barcode(client: httpx.AsyncClient, code: str) -> Optional[FoodData]:
    if not re.fullmatch(r"\d{6,14}", code):
        raise ValueError("A barcode is 6 to 14 digits")
    r = await client.get(f"{OFF_BASE}/api/v2/product/{code}.json", params={"fields": OFF_FIELDS})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if data.get("status") in (0, "failure") or not data.get("product"):
        return None
    return parse_off_product({**data["product"], "code": data.get("code", code)})


# ------------------------------------------------------------------ USDA FoodData Central
def parse_usda_food(f: dict) -> Optional[FoodData]:
    if not f.get("fdcId") or not f.get("description"):
        return None
    vals: dict[str, Optional[float]] = {}
    fallback_kcal = None
    for n in f.get("foodNutrients", []):
        nid = n.get("nutrientId") or (n.get("nutrient") or {}).get("id")
        val = n.get("value", n.get("amount"))
        unit = (n.get("unitName") or (n.get("nutrient") or {}).get("unitName") or "").lower()
        if nid in USDA_NUTRIENTS and USDA_NUTRIENTS[nid] not in vals:
            if nid == 1008 and unit and unit != "kcal":
                continue
            vals[USDA_NUTRIENTS[nid]] = _num(val)
        elif nid in USDA_ENERGY_FALLBACK and fallback_kcal is None and (not unit or unit == "kcal"):
            fallback_kcal = _num(val)
    if vals.get("kcal") is None:
        vals["kcal"] = fallback_kcal
    servings = []
    if f.get("servingSize") and str(f.get("servingSizeUnit", "")).lower() in ("g", "grm", "ml", "mlt"):
        servings.append({"label": f.get("householdServingFullText") or f"{f['servingSize']:g} g",
                         "grams": float(f["servingSize"])})
    name = f["description"].strip()
    name = name.capitalize() if name.isupper() else name
    return FoodData("usda", str(f["fdcId"]), name, f.get("brandOwner") or f.get("brandName"), f.get("gtinUpc"),
                    vals.get("kcal"), vals.get("protein"), vals.get("carbs"), vals.get("fat"), vals.get("fiber"),
                    vals.get("sugar"), vals.get("sodium_mg"), servings)


async def usda_search(client: httpx.AsyncClient, q: str, limit: int = 15) -> list[FoodData]:
    key = ("usda", fold(q))
    if key in _cache and time.time() - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    r = await client.get(f"{USDA_BASE}/foods/search", params={
        "query": q, "pageSize": limit, "dataType": "Foundation,SR Legacy,Branded", "api_key": USDA_KEY})
    if r.status_code == 429:
        raise RuntimeError("USDA rate limit reached — set your own free USDA_API_KEY")
    r.raise_for_status()
    out = [f for f in (parse_usda_food(x) for x in r.json().get("foods", [])) if f]
    _cache[key] = (time.time(), out)
    return out


# ------------------------------------------------------------------ Canadian Nutrient File
async def cnf_index(client: httpx.AsyncClient) -> list[tuple[int, Optional[str], Optional[str]]]:
    """Download the CNF food list in both languages: [(code, name_fr, name_en)]."""
    async def get(lang):
        r = await client.get(f"{CNF_BASE}/food/", params={"lang": lang, "type": "json"})
        r.raise_for_status()
        return {int(x["food_code"]): x["food_description"] for x in r.json()}
    fr, en = await asyncio.gather(get("fr"), get("en"))
    return [(code, fr.get(code), en.get(code)) for code in sorted(set(fr) | set(en))]


def parse_cnf_nutrients(code: int, name: str, rows: list[dict], servings: list[dict] | None = None) -> FoodData:
    vals: dict[str, Optional[float]] = {}
    for row in rows:
        nid = int(row.get("nutrient_name_id", -1))
        if nid in CNF_NUTRIENTS:
            vals[CNF_NUTRIENTS[nid]] = _num(row.get("nutrient_value"))
    sv = []
    for s in servings or []:
        factor = _num(s.get("conversion_factor_value"))
        if factor and s.get("measure_name"):
            sv.append({"label": s["measure_name"], "grams": round(factor * 100, 1)})   # factor is relative to 100 g
    return FoodData("cnf", str(code), name, None, None, vals.get("kcal"), vals.get("protein"), vals.get("carbs"),
                    vals.get("fat"), vals.get("fiber"), vals.get("sugar"), vals.get("sodium_mg"), sv)


async def cnf_food(client: httpx.AsyncClient, code: int, name: str, lang: str = "fr") -> FoodData:
    nut, serv = await asyncio.gather(
        client.get(f"{CNF_BASE}/nutrientamount/", params={"id": code, "lang": lang, "type": "json"}),
        client.get(f"{CNF_BASE}/servingsize/", params={"id": code, "lang": lang, "type": "json"}),
        return_exceptions=True)
    if isinstance(nut, Exception):
        raise nut
    nut.raise_for_status()
    servings = serv.json() if not isinstance(serv, Exception) and serv.status_code == 200 else []
    return parse_cnf_nutrients(code, name, nut.json(), servings)


def cnf_match(index: list[tuple[int, Optional[str], Optional[str]]], q: str, limit: int = 15) -> list[tuple[int, str]]:
    """All query words must appear (accent-insensitive); shorter names rank first."""
    words = [w for w in re.split(r"[\s,]+", fold(q)) if w]
    out = []
    for code, fr, en in index:
        for name in (fr, en):
            if name and all(w in fold(name) for w in words):
                out.append((len(name), code, name))
                break
    out.sort()
    return [(code, name) for _, code, name in out[:limit]]


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
