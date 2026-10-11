"""Bounded, read-only recipe recommendations using exact household and market facts.

Unlike the per-recipe confirmation lifecycle, discovery never allocates a
preview token, persists a plan, or authorizes a household purchase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from household_supply.application.recipe_assessment import (
    RecipeAssessment, assess_recipe, recipe_missing_request,
)
from household_supply.application.recipe_catalog import CuratedRecipe
from household_supply.application.service import ApplicationMarketError, ApplicationPlanner
from household_supply.domain import Money
from household_supply.household import HouseholdLearningService


MAX_RECOMMENDATION_QUOTES = 6


class RecipeRecommendationConflict(RuntimeError):
    """Household observations changed while recommendation prices were acquired."""


@dataclass(frozen=True, slots=True)
class RecipeRecommendations:
    recommendations: tuple[dict, ...]
    category: str
    categories: tuple[str, ...]
    budget: Money
    available_count: int


def recommend_recipes(
    *,
    recipes: tuple[CuratedRecipe, ...],
    household: HouseholdLearningService,
    planner: ApplicationPlanner,
    budget: Money,
    category: str,
    limit: int,
    clock: Callable[[], datetime],
) -> RecipeRecommendations:
    """Rank at most six matching recipes with bounded current market quotes.

    A recipe with unverified pantry ingredients is never quoted. A failed
    market acquisition cannot become an invented zero cost or a feasible
    purchase. The household history is checked after every external quote
    so stale stock cannot be marketed as current.
    """
    if not 1 <= limit <= MAX_RECOMMENDATION_QUOTES:
        raise ValueError("recommendation limit must be 1-6")
    if budget.amount < 0:
        raise ValueError("recommendation budget cannot be negative")
    categories = tuple(sorted({entry.category for entry in recipes}))
    if category != "all" and category not in categories:
        raise ValueError("unknown recipe category")
    subset = [entry for entry in recipes if category == "all" or entry.category == category]
    at = clock()
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("recommendation clock must be timezone-aware")
    history = household.history()
    state = household.state(as_of=at)
    if household.history() != history:
        raise RecipeRecommendationConflict("household changed during recipe assessment")

    inspected = [assess_recipe(entry, household=state) for entry in subset]
    # Decide which items to quote *before* contacting the market. This protects
    # the live provider from an unbounded scan of the entire recipe catalog.
    priority = {"covered": 0, "short": 1, "unverified": 2}
    inspected.sort(
        key=lambda a: (
            priority[a.status],
            a.missing_count,
            a.recipe.recipe.name,
            a.recipe.recipe.id,
        )
    )
    shortlisted = inspected[:limit]
    output: list[dict] = []
    for assessment in shortlisted:
        recipe = assessment.recipe
        status = assessment.status
        estimated: dict | None = None
        if status == "covered":
            verdict = "on_hand"
            estimated = {"amount": "0", "currency": budget.currency}
        elif status == "unverified":
            verdict = "inventory_unverified"
        else:
            request = recipe_missing_request(assessment, budget=budget)
            assert request is not None
            try:
                result = planner.plan(request)
            except ApplicationMarketError:
                verdict = "market_unavailable"
            else:
                if result.plan.status.value == "feasible":
                    cost = result.plan.total_cost
                    if cost.currency != budget.currency or cost.amount > budget.amount:
                        raise ValueError("planner returned price outside hard budget")
                    verdict = "within_budget"
                    estimated = {
                        "amount": format(cost.amount.normalize(), "f"),
                        "currency": cost.currency,
                    }
                else:
                    # No claim that this is *solely* a budget issue. Offers
                    # could be missing, unavailable or incompatible.
                    verdict = "not_feasible"
        if household.history() != history:
            raise RecipeRecommendationConflict("household changed during market quote")
        output.append({
            "recipe_id": recipe.recipe.id,
            "name": recipe.recipe.name,
            "category": recipe.category,
            "cuisine": recipe.cuisine,
            "servings": str(recipe.recipe.servings),
            "pantry_status": status,
            "verdict": verdict,
            "missing_count": assessment.missing_count,
            "unverified_count": sum(
                ingredient.status == "unverified" for ingredient in assessment.ingredients
            ),
            "estimated_purchase_cost": estimated,
            "ingredients": [
                {
                    "item_id": ingredient.item_id,
                    "required": {
                        "amount": str(ingredient.required.amount),
                        "unit": ingredient.required.unit,
                    },
                    "recorded_on_hand": (
                        None if ingredient.recorded_on_hand is None
                        else {
                            "amount": str(ingredient.recorded_on_hand.amount),
                            "unit": ingredient.recorded_on_hand.unit,
                        }
                    ),
                    "missing": (
                        None if ingredient.missing is None
                        else {
                            "amount": str(ingredient.missing.amount),
                            "unit": ingredient.missing.unit,
                        }
                    ),
                    "status": ingredient.status,
                }
                for ingredient in assessment.ingredients
            ],
            "checkout_confirmed": False,
            "plan_saved": False,
        })
    rank = {
        "on_hand": 0,
        "within_budget": 1,
        "not_feasible": 2,
        "market_unavailable": 3,
        "inventory_unverified": 4,
    }
    output.sort(
        key=lambda item: (
            rank[item["verdict"]],
            item["missing_count"],
            item["unverified_count"],
            item["estimated_purchase_cost"]["amount"] if item["estimated_purchase_cost"] else "",
            item["name"],
            item["recipe_id"],
        )
    )
    return RecipeRecommendations(
        tuple(output), category, categories, budget, len(subset),
    )
