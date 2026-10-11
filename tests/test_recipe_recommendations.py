"""Issue #28: grounded recipe suggestions, safe budget/cost and handoff semantics."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from examples.m11_local_web import build_demo_app
from household_supply.application.service import ApplicationMarketError


def _stock(api, item, amount, unit, number):
    response = api.handle("POST", "/household/stocktakes", {
        "event_id": f"recommend-stock-{number}",
        "item_id": item,
        "quantity": {"amount": amount, "unit": unit},
        "reason": "checked at home",
    })
    assert response.status == 201, response.body


def _recommend(api, amount="120", category="завтрак", limit=6):
    return api.handle("POST", "/recipes/recommend", {
        "budget": {"amount": amount, "currency": "KGS"},
        "category": category, "limit": limit,
    })


def test_unknown_stock_is_not_assumed_zero_or_quoted(tmp_path):
    app = build_demo_app(tmp_path)
    before = app.api.handle("GET", "/household/history").body
    response = _recommend(app.api)
    assert response.status == 200, response.body
    result = response.body
    assert result["estimate_only"] is True
    assert result["inventory_updated"] is False
    assert result["plan_saved"] is False
    assert result["checkout_confirmed"] is False
    assert result["available_count"] == 1
    assert result["returned_count"] == 1
    assert result["categories"] == ["завтрак"]
    candidate = result["recommendations"][0]
    assert candidate["recipe_id"] == "milk-rice"
    assert candidate["verdict"] == "inventory_unverified"
    assert candidate["unverified_count"] == 2
    assert candidate["estimated_purchase_cost"] is None
    assert app.api.handle("GET", "/household/history").body == before
    assert app.api.handle("GET", "/plans?limit=10").body["plans"] == []
    assert app.api.recipe_api.confirmation.pending == {}


def test_recommendations_cover_zero_cost_budgeted_and_unavailable_cases(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    _stock(api, "rice", "1", "kg", 1)
    _stock(api, "milk", "100", "ml", 2)
    before = api.handle("GET", "/household/history").body
    affordable = _recommend(api, "120")
    assert affordable.status == 200, affordable.body
    candidate = affordable.body["recommendations"][0]
    assert candidate["verdict"] == "within_budget"
    assert candidate["estimated_purchase_cost"] == {"amount": "120", "currency": "KGS"}
    assert candidate["missing_count"] == 1
    assert {x["item_id"] for x in candidate["ingredients"] if x["status"] == "short"} == {"milk"}
    assert api.handle("GET", "/household/history").body == before
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []
    assert api.recipe_api.confirmation.pending == {}

    insufficient = _recommend(api, "100")
    assert insufficient.status == 200, insufficient.body
    candidate = insufficient.body["recommendations"][0]
    assert candidate["verdict"] == "not_feasible"
    assert candidate["estimated_purchase_cost"] is None

    _stock(api, "milk", "1", "l", 3)
    covered = _recommend(api, "0")
    assert covered.status == 200, covered.body
    candidate = covered.body["recommendations"][0]
    assert candidate["verdict"] == "on_hand"
    assert candidate["estimated_purchase_cost"] == {"amount": "0", "currency": "KGS"}


def test_recommendation_only_filters_exact_category_and_limits_bound(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    assert _recommend(api, category="all", limit=1).status == 200
    assert _recommend(api, category="обед").status == 422
    assert api.handle("GET", "/recipes/recommend").status == 405
    assert api.handle("POST", "/recipes/recommend?bad=1", {}).status == 400
    for payload in (
        None, {}, {"budget": {"amount": "-1", "currency": "KGS"}},
        {"budget": {"amount": "100", "currency": "KGS"}, "limit": True},
        {"budget": {"amount": "100", "currency": "KGS"}, "limit": 7},
        {"budget": {"amount": "100", "currency": "KGS"}, "category": "unsupported"},
        {"budget": {"amount": "100", "currency": "KGS"}, "extra": "fake_cost"},
    ):
        bad = api.handle("POST", "/recipes/recommend", payload)
        assert bad.status == 422, (payload, bad.body)


def test_failed_market_quote_is_unknown_price_not_false_affordable(tmp_path):
    app = build_demo_app(tmp_path)
    _stock(app.api, "rice", "1", "kg", 1)
    _stock(app.api, "milk", "100", "ml", 2)

    class Unavailable:
        catalog = app.api.catalog

        def plan(self, request):
            raise ApplicationMarketError("seller did not return verified offers")

    api = replace(
        app.api,
        recipe_api=replace(app.api.recipe_api, planner=Unavailable()),
    )
    response = _recommend(api)
    assert response.status == 200, response.body
    candidate = response.body["recommendations"][0]
    assert candidate["verdict"] == "market_unavailable"
    assert candidate["estimated_purchase_cost"] is None
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_household_mutation_during_quote_fails_closed(tmp_path):
    app = build_demo_app(tmp_path)
    _stock(app.api, "rice", "1", "kg", 1)
    _stock(app.api, "milk", "100", "ml", 2)
    original = app.api.recipe_api.planner

    class Changing:
        catalog = app.api.catalog

        def plan(self, request):
            _stock(app.api, "milk", "200", "ml", 3)
            return original.plan(request)

    api = replace(
        app.api,
        recipe_api=replace(app.api.recipe_api, planner=Changing()),
    )
    response = _recommend(api)
    assert response.status == 409, response.body
    assert response.body["error"] == "stale_recipe_recommendation"
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []
