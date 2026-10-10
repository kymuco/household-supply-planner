"""Read-only recipe suitability from exact recipe demands and household facts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from household_supply.demand import MealDemandSource, compile_demand_sources
from household_supply.domain import MealRequest, Money, Quantity
from household_supply.domain._decimal import subtract_decimals_exact
from household_supply.household import HouseholdState

from .models import ApplicationPlanRequest, RequestedItem
from .recipe_catalog import CuratedRecipe


@dataclass(frozen=True, slots=True)
class RecipeIngredientAssessment:
    item_id: str
    required: Quantity
    recorded_on_hand: Quantity | None
    missing: Quantity | None
    status: str  # "covered", "short", "unverified"


@dataclass(frozen=True, slots=True)
class RecipeAssessment:
    recipe: CuratedRecipe
    ingredients: tuple[RecipeIngredientAssessment, ...]
    status: str  # "covered", "short", "unverified"
    missing_demands: tuple[RequestedItem, ...]

    @property
    def missing_count(self) -> int:
        return len(self.missing_demands)


def assess_recipe(recipe: CuratedRecipe, *, household: HouseholdState) -> RecipeAssessment:
    """Unknown quantity is not zero: no quote can be authoritative for it."""
    source = MealDemandSource(
        f"recipe:{recipe.recipe.id}", (MealRequest(recipe.recipe, recipe.recipe.servings),)
    )
    compilation = compile_demand_sources((source,))
    ingredients: list[RecipeIngredientAssessment] = []
    missing: list[RequestedItem] = []
    for demand in compilation.demands:
        required = demand.quantity.as_base()
        observed = household.quantity_for(demand.item.id)
        if observed is None:
            ingredients.append(RecipeIngredientAssessment(
                demand.item.id, required, None, None, "unverified"
            ))
            continue
        if not observed.compatible_with(required):
            raise ValueError(f"household and recipe units disagree: {demand.item.id}")
        on_hand = observed.as_base()
        needed_amount = subtract_decimals_exact(required.amount, on_hand.amount)
        if needed_amount > 0:
            deficit = Quantity(needed_amount, required.unit)
            missing.append(RequestedItem(demand.item.id, deficit))
            status = "short"
        else:
            deficit = Quantity(Decimal(0), required.unit)
            status = "covered"
        ingredients.append(RecipeIngredientAssessment(
            demand.item.id, required, on_hand, deficit, status
        ))
    ingredients.sort(key=lambda x: x.item_id)
    if any(x.status == "unverified" for x in ingredients):
        state = "unverified"
    elif missing:
        state = "short"
    else:
        state = "covered"
    return RecipeAssessment(recipe, tuple(ingredients), state, tuple(missing))


def recipe_missing_request(assessment: RecipeAssessment, *, budget: Money) -> ApplicationPlanRequest | None:
    """A quote plans exact shortages only, not full demand on top of old inventory."""
    if assessment.status != "short":
        return None
    return ApplicationPlanRequest(demands=assessment.missing_demands, budget=budget)
