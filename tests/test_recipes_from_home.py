from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from examples.m11_local_web import build_demo_app, build_demo_catalog
from household_supply.application.recipe_assessment import assess_recipe, recipe_missing_request
from household_supply.application.recipe_catalog import starter_recipe_pack
from household_supply.domain import Money, Quantity
from household_supply.household import HouseholdState, HouseholdBalance
from household_supply.market.catalogs.globus_demo_staples import build_globus_demo_staples_catalog


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def _set_stock(api, item_id, amount, unit, number):
    reply = api.handle("POST", "/household/stocktakes", {
        "event_id": f"recipe-stock-{number}",
        "item_id": item_id,
        "quantity": {"amount": amount, "unit": unit},
        "reason": "observed during kitchen check",
    })
    assert reply.status == 201, reply.body


def _quote(api, recipe_id="milk-rice", amount="1000"):
    return api.handle("POST", f"/recipes/{recipe_id}/quote", {
        "budget": {"amount": amount, "currency": "KGS"}
    })


def test_recipe_pack_is_catalog_scoped_and_future_cuisine_ready():
    offline, _ = build_demo_catalog()
    local_recipes = starter_recipe_pack(offline)
    assert [x.recipe.id for x in local_recipes] == ["milk-rice"]
    for recipe in local_recipes:
        assert recipe.cuisine and recipe.category and recipe.steps
        assert recipe.attribution is None
        assert recipe.source_url is None
        for ingredient in recipe.recipe.ingredients:
            assert ingredient.quantity.amount > 0
    globus, _ = build_globus_demo_staples_catalog()
    recipes = starter_recipe_pack(globus)
    assert len(recipes) >= 9
    assert len({x.recipe.id for x in recipes}) == len(recipes)
    assert all(x.attribution is None for x in recipes)
    assert all(x.recipe.ingredients for x in recipes)


def test_unobserved_stock_is_unknown_not_zero_and_market_is_not_queried(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    data = api.handle("GET", "/recipes")
    assert data.status == 200, data.body
    recipe = data.body["recipes"][0]
    assert recipe["recipe_id"] == "milk-rice"
    assert recipe["status"] == "unverified"
    assert recipe["missing_count"] == 0
    assert {x["status"] for x in recipe["ingredients"]} == {"unverified"}
    before = api.handle("GET", "/household/history").body
    q = _quote(api)
    assert q.status == 200
    assert q.body["recipe"]["status"] == "unverified"
    assert q.body["estimated_purchase_cost"] is None
    assert q.body["plan"] is None
    assert api.handle("GET", "/household/history").body == before
    assert api.handle("GET", "/plans?limit=12").body["plans"] == []


def test_known_shortfall_costs_whole_market_packages_not_partial_units(tmp_path):
    api = build_demo_app(tmp_path).api
    _set_stock(api, "rice", "1", "kg", 1)
    _set_stock(api, "milk", "100", "ml", 2)
    quote = _quote(api)
    assert quote.status == 200, quote.body
    body = quote.body
    assert body["recipe"]["status"] == "short"
    assert body["recipe"]["missing_count"] == 1
    ingredients = {x["item_id"]: x for x in body["recipe"]["ingredients"]}
    assert ingredients["milk"]["missing"] == {"amount": "300", "unit": "ml"}
    assert ingredients["rice"]["status"] == "covered"
    assert body["plan"]["status"] == "feasible"
    assert body["estimated_purchase_cost"] == {"amount": "120", "currency": "KGS"}
    assert len(body["plan"]["purchases"]) == 1
    assert body["plan"]["purchases"][0]["packs"] == 1
    assert api.handle("GET", "/plans?limit=12").body["plans"] == []
    assert api.handle("GET", "/household/history").body["event_count"] == 2


def test_zero_is_observed_absence_and_full_coverage_has_zero_cost(tmp_path):
    api = build_demo_app(tmp_path).api
    _set_stock(api, "rice", "0", "kg", 1)
    _set_stock(api, "milk", "0", "l", 2)
    empty = _quote(api)
    assert empty.status == 200
    assert empty.body["recipe"]["status"] == "short"
    assert empty.body["recipe"]["missing_count"] == 2
    _set_stock(api, "rice", "1", "kg", 3)
    _set_stock(api, "milk", "1", "l", 4)
    full = _quote(api)
    assert full.status == 200
    assert full.body["recipe"]["status"] == "covered"
    assert full.body["estimated_purchase_cost"] == {"amount": "0", "currency": "KGS"}
    assert full.body["plan"] is None
    assert api.handle("GET", "/plans?limit=12").body["plans"] == []


def test_budget_shortfall_uses_planner_infeasibility(tmp_path):
    api = build_demo_app(tmp_path).api
    _set_stock(api, "rice", "0", "kg", 1)
    _set_stock(api, "milk", "0", "l", 2)
    quote = _quote(api, amount="0")
    assert quote.status == 200
    assert quote.body["recipe"]["status"] == "short"
    assert quote.body["plan"]["status"] == "infeasible"
    assert quote.body["estimated_purchase_cost"] is None


def test_recipe_endpoint_rejects_unknown_unsupported_and_bad_methods(tmp_path):
    api = build_demo_app(tmp_path).api
    assert api.handle("POST", "/recipes", {}).status == 405
    assert api.handle("GET", "/recipes/milk-rice/quote").status == 405
    assert api.handle("POST", "/recipes/unknown/quote", {}).status == 404
    assert api.handle("GET", "/recipes?x=1").status == 400
    for payload in [None, {}, {"budget": {"amount": "-1", "currency": "KGS"}},
                    {"budget": {"amount": "1", "currency": "KGS"}, "extra": True}]:
        response = api.handle("POST", "/recipes/milk-rice/quote", payload)
        assert response.status == 422, response.body


def test_recipe_coverage_is_pure_deterministic_with_known_and_unknown():
    catalog, _ = build_demo_catalog()
    recipe = starter_recipe_pack(catalog)[0]
    rice = next(sku.item for sku in catalog.skus if sku.item.id == "rice")
    state = HouseholdState(NOW, (HouseholdBalance(rice, Quantity("1", "kg")),), ())
    a = assess_recipe(recipe, household=state)
    assert a == assess_recipe(recipe, household=state)
    assert a.status == "unverified"
    assert [i.item_id for i in a.ingredients if i.status == "unverified"] == ["milk"]
    assert recipe_missing_request(a, budget=Money(1000, "KGS")) is None


def test_recipes_refresh_after_stocktake_without_persisted_recipe_events(tmp_path):
    api = build_demo_app(tmp_path).api
    initial = api.handle("GET", "/recipes").body
    _set_stock(api, "rice", "1", "kg", 1)
    _set_stock(api, "milk", "0", "l", 2)
    next_state = api.handle("GET", "/recipes").body
    assert initial["recipes"][0]["status"] == "unverified"
    assert next_state["recipes"][0]["status"] == "short"
    assert api.handle("GET", "/household/history").body["event_count"] == 2
