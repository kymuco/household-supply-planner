# M12.20 — Issue #28: pantry- and budget-aware recipe recommendations

This milestone completes the **recommendation** portion of upstream issue
[MirriMmann/household-supply-planner#28](https://github.com/MirriMmann/household-supply-planner/issues/28)
without replacing the established M12.8/M12.9 recipe quote and confirmation path.

## User workflow

1. In **«Что приготовить из того, что есть дома?»** select a recipe
   **category** (or all categories) and a **KGS budget** for missing groceries.
2. Press **«Подобрать блюда под бюджет»**. HSM uses canonical curated recipe
   IDs, measured ingredients and the current recorded household inventory.
3. HSM ranks up to **six** matching recipes and shows for every ingredient:
   full required quantity, recorded on-hand status and known shortage.
   Recommendations are ordered as:
   - `on_hand`: already covered; additional cost is exactly zero.
   - `within_budget`: a currently feasible complete **whole-package**
     procurement quote under the requested budget.
   - `not_feasible`: no complete plan for the allowed budget or offers;
     do not misdiagnose missing retailer stock as “too expensive.”
   - `market_unavailable`: unable to get trustworthy seller prices.
   - `inventory_unverified`: one or more pantry values unknown; no quote.
4. Select **«Открыть рецепт и проверить план»** to open the existing detailed
   recipe with the reviewed budget carried into the ordinary planner form.
   The full ingredient list, original authored steps, and exact current
   `/recipes/<id>/quote` / `/recipes/<id>/confirm` two-step workflow
   remain in force.
5. Only a **separate explicit** confirmation saves a shortage `PlanRecord`;
   recommendations and quotations never record a completed purchase or
   change household inventory. A saved plan does not imply checkout.

## HTTP contract

```http
POST /recipes/recommend
Content-Type: application/json

{
  "budget": {"amount": "120", "currency": "KGS"},
  "category": "завтрак",
  "limit": 6
}
```

The response includes `recommendations`, actual matching
`categories`, `available_count`, `returned_count`, typed
`estimated_purchase_cost` or `null`, per-ingredient ground truth
and `estimate_only=true`, `plan_saved=false`,
`inventory_updated=false`, `checkout_confirmed=false`.

Unknown category/extra parameters/incorrect limit or budget are 422.
Query strings and absolute request-targets remain prohibited.
A changing household history while quotes are fetched returns 409,
not a stale recommendation.

## Market cost and safety policy

- Recommendation candidate selection is deterministic before market access:
  sorted known-on-hand/short/unverified + fewest deficits + stable recipe name.
  Only the top 1–6 candidates are priced, **not the entire catalog**.
  The caller can request a category to narrow the shortlist.
- Pricing uses the **existing typed `ApplicationPlanner.plan()`** with a
  single recipe's exact missing demands and hard budget; no proportional
  ingredient cost approximation and no new seller identity matching.
- If an ingredient has unknown on-hand amount, it is not assumed absent
  or free and the recipe is **not** sent to a market planner.
- Price acquisition failure is `market_unavailable`, never success.
- Saving still needs a separate fresh exact `recipe/quote` with its
  expiring one-time preview identity and a distinct confirmation. These
  semantics also prevent stale recommendation quotes from bypassing approval.
- This is a recipe catalog recommendation feature, not a promise that every
  Kyrgyz dish is purchasable; full recipe definitions with unsupported
  ingredients remain discoverable in the M12.14 shared readiness flow.
  Public Globus has no verified per-item sale-minimum/step for weighted items.

## Tests

Python suite: missing pantry inventory, no silent plan writes, complete
package quote inside budget, infeasible budget, covered zero additional
cost, unknown categories/bad parameters, market failures and household
mutation during quote.

Mobile Chromium at 390×844: select category/budget, unknown-stock
recommendations, fresh physical stocktakes, re-run suggestions,
open the exact recipe details, quote it again and save the plan only
after a separate confirmation. Original shopping/stocktake/mobile gates
continue unchanged.

**Current limitation:** this version quotes at most six selected
catalog-covered recipes per request to bound retailer API calls. It does
not attempt unbounded cross-catalog global lowest-price selection,
personalized taste learning, OCR, or store checkout.
