"""Local read-only recipe discovery and exact missing-ingredient quotations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from household_supply.application.json_api import (
    JsonApiResponse, JsonPayloadError, _parse_money, _require_keys, _require_mapping,
    serialize_plan_result,
)
from household_supply.application.models import ApplicationRequestError
from household_supply.application.recipe_assessment import (
    RecipeAssessment, assess_recipe, recipe_missing_request,
)
from household_supply.application.recipe_catalog import CuratedRecipe, starter_recipe_pack
from household_supply.application.service import ApplicationMarketError, ApplicationPlanner
from household_supply.domain import Money, Quantity
from household_supply.household import HouseholdLearningService


def _quantity(value: Quantity | None) -> dict[str, str] | None:
    return None if value is None else {"amount": format(value.amount.normalize(), "f"), "unit": value.unit}


def _recipe_body(assessment: RecipeAssessment) -> dict[str, Any]:
    recipe = assessment.recipe
    return {
        "recipe_id": recipe.recipe.id,
        "name": recipe.recipe.name,
        "servings": str(recipe.recipe.servings),
        "category": recipe.category,
        "cuisine": recipe.cuisine,
        "source_url": recipe.source_url,
        "attribution": recipe.attribution,
        "steps": list(recipe.steps),
        "status": assessment.status,
        "missing_count": assessment.missing_count,
        "ingredients": [
            {
                "item_id": entry.item_id,
                "required": _quantity(entry.required),
                "recorded_on_hand": _quantity(entry.recorded_on_hand),
                "missing": _quantity(entry.missing),
                "status": entry.status,
            }
            for entry in assessment.ingredients
        ],
    }


@dataclass(frozen=True, slots=True)
class RecipeWebApi:
    household: HouseholdLearningService
    planner: ApplicationPlanner
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def accepts_json_body(self, method: str, path: str) -> bool:
        parts = urlsplit(path)
        return (
            method.strip().upper() == "POST"
            and parts.path.startswith("/recipes/")
            and parts.path.endswith("/quote")
        )

    def handle(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None,
    ) -> JsonApiResponse:
        url = urlsplit(path)
        if url.scheme or url.netloc or url.fragment or url.query:
            return JsonApiResponse(400, {"error": "invalid_request_target"})
        method = method.strip().upper()
        pack = starter_recipe_pack(self.planner.catalog)
        lookup = {entry.recipe.id: entry for entry in pack}
        if url.path == "/recipes":
            if method != "GET":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            state = self.household.state(as_of=self.clock())
            results = [assess_recipe(recipe, household=state) for recipe in pack]
            priority = {"covered": 0, "short": 1, "unverified": 2}
            results.sort(
                key=lambda value: (priority[value.status], value.missing_count, value.recipe.recipe.name)
            )
            return JsonApiResponse(
                200,
                {
                    "recipes": [_recipe_body(a) for a in results],
                    "inventory_basis": "recorded_household_state",
                    "notice": "Остатки основаны на последних записях и могут требовать проверки.",
                },
            )

        parts = url.path.split("/")
        if len(parts) != 4 or parts[1] != "recipes" or parts[3] != "quote":
            return JsonApiResponse(404, {"error": "not_found"})
        recipe = lookup.get(parts[2])
        if recipe is None:
            return JsonApiResponse(404, {"error": "recipe_not_in_catalog"})
        if method != "POST":
            return JsonApiResponse(405, {"error": "method_not_allowed"})
        try:
            obj = _require_mapping(payload, label="recipe quote")
            _require_keys(obj, label="recipe quote", required={"budget"})
            budget = _parse_money(obj["budget"], label="budget")
            if budget.amount < 0:
                raise JsonPayloadError("recipe budget cannot be negative")
            assessment = assess_recipe(
                recipe, household=self.household.state(as_of=self.clock())
            )
            data = {
                "recipe": _recipe_body(assessment),
                "estimate_only": True,
                "plan": None,
                "estimated_purchase_cost": None,
                "budget": {"amount": str(budget.amount), "currency": budget.currency},
            }
            if assessment.status == "covered":
                data["estimated_purchase_cost"] = _quantity_cost(Money(0, budget.currency))
            request = recipe_missing_request(assessment, budget=budget)
            if request is not None:
                result = self.planner.plan(request)
                data["plan"] = serialize_plan_result(result)
                if result.plan.status.value == "feasible":
                    data["estimated_purchase_cost"] = _quantity_cost(result.plan.total_cost)
            return JsonApiResponse(200, data)
        except (ValueError, TypeError, JsonPayloadError, ApplicationRequestError) as exc:
            return JsonApiResponse(422, {"error": "invalid_request", "detail": str(exc)})
        except ApplicationMarketError as exc:
            return JsonApiResponse(502, {"error": "market_acquisition_failed", "detail": str(exc)})


def _quantity_cost(value: Money) -> dict[str, str]:
    return {"amount": str(value.amount), "currency": value.currency}
