# Recipe packs: product and provenance rules (M12.8 onward)

This is a **roadmap for expanding** Cook from Home, not a claim that the
starter pack covers national cuisines or famous chefs.

## Product order

1. Ship a small **original** tested starter catalog of exact ingredients and
   servings. Show `covered`, `short`, and `unverified` from household records,
   and quote *whole purchasable packages* only for confirmed shortages.
2. Introduce independent cuisine packs with reviewable provenance. Start with
   **Kyrgyz** home recipes (e.g., regional variations of boorsok, manty,
   lagman, kuurdak, oromo) when the Item catalog truly contains every measured
   ingredient; then expand to other cuisines. Do not mislabel a simplified
   dish as a definitive traditional version.
3. Add a **chef/source-link collection** for recipes by chefs such as
   Gordon Ramsay or Nick DiGiovanni: title, creator, publisher, verified
   source URL and link-out. Do not present third-party material as ours.
   Reproduce creative recipe descriptions, photos or full instructions only
   under an appropriate license/permission; otherwise write an independently
   authored brief variation or link to the original.

## Invariants of a recipe pack

- Stable `recipe.id`, readable name, servings, cuisine/category, explicit
  measured canonical Item requirements, unit-compatible SKU coverage.
- Do not silently remove an unavailable ingredient to make a recipe appear
  purchasable. If an ingredient is not in the local catalog, withhold the
  recipe until that Item is supported or explain unsupported status.
- No invisible default pantry staples. Water, oil, salt, flour and spices
  count as requirements when needed; for untracked ingredients ask or mark
  `unverified`, never assume an infinite stock.
- Cost for a recipe is **conditional** on confirmed inventory and current,
  correctly matched retailer packages. It is not the price of the ingredient
  fractions and never an automatically authorized purchase.
- Ingredient alternatives/substitutions must be explicit user choices with
  validated compatibility and quantities, not heuristic fuzzy SKU matches.
- Maintain source/creator/license status, authored instructions, verified
  ingredient quantities, date reviewed and quality notes in a future
  versioned content pack. Current starter recipes are original standalone
  demonstrations, not licensed reproductions or endorsements.
- Plan persistence should carry recipe+servings and exact demand provenance,
  use the existing PlanRecord validation, and require an **explicit user
  confirmation**; it must not create PurchaseEvents on suggestion.

## Pilot criteria before importing large catalogs

Measure useful recipe coverage given the real local retailer catalog,
fraction of `unverified` cases, verified home-only recommendations, total
cost and infeasibility explanation accuracy, and time from recipe discovery
to an explicitly confirmed shopping list. Expand catalog content based on
gaps observed in this pilot, not just total recipe count.
