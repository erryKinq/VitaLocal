"""Open Food Facts client: product by barcode and text search, mapped onto the food catalog.

Only called on demand (a scanned barcode, the "search online" button), never per keystroke; a hit
is stored in the own catalog (`food`, source `off`) by the caller. Limits as OFF asks of API
users: 15 product and 10 search requests per minute, a User-Agent with a contact
(`VITALOCAL_OFF_CONTACT`).

Mapping: OFF's `<nutrient>_100g` values are per 100 g (or 100 ml for liquids) and normalized to
grams for every mass nutrient, so they are converted to the catalog unit (mg, µg). Exceptions read
by their `_unit` field: energy (kcal, else kJ / 4.184) and alcohol (% vol, converted to g at
0.789 g/ml). A value OFF does not have stays missing, never 0.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque

import httpx

from vitalocal import __version__
from vitalocal.config import get_settings
from vitalocal.nutrients import NUTRIENTS

PRODUCT_PER_MINUTE = 15
SEARCH_PER_MINUTE = 10
TIMEOUT_S = 8.0
_FIELDS = (
    "code,product_name,product_name_de,generic_name,brands,quantity,product_quantity,"
    "product_quantity_unit,serving_size,serving_quantity,nutrition_data_per,nutriments,image_front_small_url"
)
_UNIT_FACTOR = {"g": 1.0, "mg": 1000.0, "µg": 1_000_000.0}
ETHANOL_G_PER_ML = 0.789

# Tests replace this with an httpx.MockTransport (no network in tests).
_TRANSPORT: httpx.BaseTransport | None = None


class OffUnavailable(Exception):
    """OFF is switched off, unreachable, or answered with an error."""


class OffRateLimited(Exception):
    """Our own per-minute budget is used up; try again shortly."""


class _Window:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def take(self) -> None:
        now = time.monotonic()
        with self.lock:
            while self.calls and now - self.calls[0] >= 60:
                self.calls.popleft()
            if len(self.calls) >= self.per_minute:
                raise OffRateLimited(f"at most {self.per_minute} Open Food Facts requests per minute")
            self.calls.append(now)


_product_window = _Window(PRODUCT_PER_MINUTE)
_search_window = _Window(SEARCH_PER_MINUTE)


def reset_limits() -> None:
    _product_window.calls.clear()
    _search_window.calls.clear()


def _client() -> httpx.Client:
    s = get_settings()
    if not s.off_enabled:
        raise OffUnavailable("Open Food Facts is switched off (VITALOCAL_OFF_ENABLED)")
    contact = f" ({s.off_contact})" if s.off_contact else ""
    return httpx.Client(
        base_url=s.off_base_url,
        timeout=TIMEOUT_S,
        headers={"User-Agent": f"VitaLocal/{__version__}{contact}"},
        transport=_TRANSPORT,
    )


def _get(path: str, params: dict) -> httpx.Response:
    try:
        with _client() as c:
            return c.get(path, params=params)
    except httpx.HTTPError as e:
        raise OffUnavailable(f"Open Food Facts not reachable: {type(e).__name__}") from e


def _json(r: httpx.Response) -> dict:
    """OFF answers overload with an HTML page (HTTP 200)."""
    try:
        data = r.json()
    except ValueError as e:
        raise OffUnavailable("Open Food Facts is temporarily unavailable") from e
    if not isinstance(data, dict):
        raise OffUnavailable("unexpected answer from Open Food Facts")
    return data


def normalize_barcode(code: str) -> str:
    digits = re.sub(r"\D", "", code or "")
    if not 6 <= len(digits) <= 14:
        raise ValueError("a barcode has 6 to 14 digits")
    return digits


def product(code: str) -> dict | None:
    """The product as a food draft, or None when OFF does not know the barcode."""
    _product_window.take()
    r = _get(f"/api/v2/product/{code}.json", {"fields": _FIELDS})
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise OffUnavailable(f"Open Food Facts answered HTTP {r.status_code}")
    data = _json(r)
    if data.get("status") != 1 or not isinstance(data.get("product"), dict):
        return None
    return to_food(data["product"], code)


def search(q: str, limit: int = 20) -> list[dict]:
    _search_window.take()
    r = _get(
        "/cgi/search.pl",
        {
            "search_terms": q,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": max(1, min(limit, 50)),
            "fields": _FIELDS,
        }
        | (
            # products sold in that country only (OFF's "countries" tag)
            {"tagtype_0": "countries", "tag_contains_0": "contains", "tag_0": country}
            if (country := get_settings().off_country)
            else {}
        ),
    )
    if r.status_code >= 400:
        raise OffUnavailable(f"Open Food Facts answered HTTP {r.status_code}")
    out = []
    for p in _json(r).get("products") or []:
        if isinstance(p, dict) and p.get("code"):
            food = to_food(p, str(p["code"]))
            if food["name"]:
                out.append(food)
    return out


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "."))
        except ValueError:
            return None
    return None


def _is_liquid(p: dict) -> bool:
    unit = str(p.get("product_quantity_unit") or "").lower()
    if unit:
        return unit in ("ml", "l", "cl", "dl")
    return bool(re.search(r"\d\s*(ml|cl|dl|l)\b", str(p.get("quantity") or "").lower()))


def nutrients_of(nutriments: dict, suffix: str = "_100g") -> dict[str, float]:
    out: dict[str, float] = {}
    for n in NUTRIENTS:
        if n.off_field is None or n.key in ("calories", "kilojoules", "alcohol"):
            continue
        v = _num(nutriments.get(n.off_field + suffix))
        if v is not None and n.unit in _UNIT_FACTOR:
            out[n.key] = round(v * _UNIT_FACTOR[n.unit], 4)
    kcal = _num(nutriments.get("energy-kcal" + suffix))
    kj = _num(nutriments.get("energy-kj" + suffix))
    if kj is None and str(nutriments.get("energy_unit") or "kJ").lower() == "kj":
        kj = _num(nutriments.get("energy" + suffix))
    if kcal is None and kj is not None:
        kcal = kj / 4.184
    if kcal is not None:
        out["calories"] = round(kcal, 2)
    if kj is not None:
        out["kilojoules"] = round(kj, 2)
    alcohol = _num(nutriments.get("alcohol" + suffix))
    if alcohol is not None:
        unit = str(nutriments.get("alcohol_unit") or "% vol").lower()
        out["alcohol"] = round(alcohol * ETHANOL_G_PER_ML if "%" in unit else alcohol, 3)
    return out


def to_food(p: dict, code: str) -> dict:
    """An OFF product as a catalog draft (no id): per 100 g/ml, with the serving as a portion."""
    nutriments = p.get("nutriments") or {}
    unit = "ml" if _is_liquid(p) else "g"
    nutrients, base = nutrients_of(nutriments), 100.0
    serving = _num(p.get("serving_quantity"))
    if not nutrients and serving:
        # only per-serving values declared
        nutrients, base = nutrients_of(nutriments, "_serving"), serving
    portions = []
    if serving and serving > 0:
        label = str(p.get("serving_size") or "").strip() or f"{serving:g} {unit}"
        portions.append({"label": f"1 Portion ({label})"[:60], "amount": serving})
    package = _num(p.get("product_quantity"))
    if package and package > 0 and package != serving and package <= 5000:
        portions.append({"label": f"Packung ({package:g} {unit})", "amount": package})
    name = (p.get("product_name_de") or p.get("product_name") or p.get("generic_name") or "").strip()
    brand = (str(p.get("brands") or "").split(",")[0]).strip() or None
    return {
        "name": name[:200],
        "brand": brand,
        "barcode": code,
        "base_amount": base,
        "base_unit": unit,
        "portions": portions,
        "nutrients": nutrients,
        "source": "off",
        "source_ref": code,
        "source_data": {
            k: p.get(k)
            for k in (
                "code", "product_name", "brands", "quantity", "serving_size", "serving_quantity",
                "nutrition_data_per", "image_front_small_url",
            )
            if p.get(k) is not None
        }
        | {"nutriments": {k: v for k, v in nutriments.items() if k.endswith(("_100g", "_serving", "_unit"))}},
    }  # fmt: skip
