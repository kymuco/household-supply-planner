"use strict";

// M12.20 / #28. Existing recipe cards remain the authority to request a
// fresh quote and explicitly save a plan. Suggestions are read-only.
(() => {
  const by = (id) => document.getElementById(id);
  const form = by("recipe-recommend-form");
  const status = by("recipe-recommend-status");
  const results = by("recipe-recommend-results");
  const categories = by("recipe-recommend-category");
  const budget = by("recipe-recommend-budget");
  const button = by("recipe-recommend-submit");
  let revision = 0;
  let pending = false;

  function invalidate() {
    ++revision;
    results.replaceChildren();
    status.textContent = "";
  }
  function numberValue(input) {
    const value = String(input).trim().replace(/[\s\u00a0]/g, "").replace(",", ".");
    if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value) || value.length > 32) {
      throw new Error("Введите неотрицательный бюджет в сомах.");
    }
    return value;
  }
  function line(parent, tag, value, className) {
    const node = document.createElement(tag);
    node.textContent = String(value);
    if (className) node.className = className;
    parent.appendChild(node);
    return node;
  }
  function quantity(q) {
    return q ? `${q.amount} ${q.unit}` : "—";
  }
  function labelFor(id) {
    const option = window.HSMRecipeRecommendationNames?.get(id);
    return option || id;
  }
  function reason(item) {
    if (item.verdict === "on_hand") return "Все ингредиенты есть по учёту; докупать не нужно.";
    if (item.verdict === "within_budget") {
      return `Докупка целых упаковок: ориентировочно ${item.estimated_purchase_cost.amount} сом, внутри бюджета.`;
    }
    if (item.verdict === "inventory_unverified") {
      return `Нужно уточнить остатки ${item.unverified_count} ингредиентов; стоимость неизвестна.`;
    }
    if (item.verdict === "market_unavailable") {
      return "Не удалось получить цены магазина — покупку нельзя оценить.";
    }
    return "Нет полного доступного плана в этом бюджете или нет нужных предложений.";
  }
  function openRecipe(item) {
    const match = [...by("recipes-list").querySelectorAll(".recipe-card")]
      .find(card => card.dataset.recipeId === item.recipe_id);
    if (!match) {
      status.textContent = "Этот рецепт больше не доступен в каталоге. Обновите список.";
      return;
    }
    const globalBudget = by("plan-budget");
    // The existing exact recipe quote uses the main planner's budget.
    // Keep it consistent with the recommendation the operator just reviewed.
    globalBudget.value = numberValue(budget.value);
    globalBudget.dispatchEvent(new Event("input", { bubbles: true }));
    match.open = true;
    match.scrollIntoView({ block: "center", behavior: "smooth" });
    const quote = match.querySelector(".recipe-actions button");
    if (quote) quote.focus({ preventScroll: true });
  }
  function render(data) {
    results.replaceChildren();
    if (!data.recommendations.length) {
      line(results, "p", "В этой категории подходящих рецептов в каталоге пока нет.");
      return;
    }
    for (const item of data.recommendations) {
      const card = line(results, "article", "", "recipe-recommend-card");
      line(card, "strong", item.name);
      line(card, "p", `${item.category} · ${item.cuisine} · ${item.servings} порции`);
      line(card, "p", reason(item));
      const ingredients = document.createElement("ul");
      for (const ingredient of item.ingredients) {
        const state = ingredient.status === "covered" ? "есть по учёту" :
          ingredient.status === "short" ? `нужно ещё ${quantity(ingredient.missing)}` :
            "остаток не проверен";
        line(ingredients, "li",
          `${labelFor(ingredient.item_id)}: ${quantity(ingredient.required)} — ${state}`);
      }
      card.appendChild(ingredients);
      const open = line(card, "button", "Открыть рецепт и проверить план", "secondary-button");
      open.type = "button";
      open.addEventListener("click", () => openRecipe(item));
    }
    status.textContent = `Показано ${data.returned_count} из ${data.available_count} рецептов. ` +
      "Это оценка на текущий момент; сохранение плана требует отдельного подтверждения.";
  }
  async function suggest(event) {
    event.preventDefault();
    if (pending) return;
    invalidate();
    let amount;
    try {
      amount = numberValue(budget.value);
    } catch (error) {
      status.textContent = error.message;
      return;
    }
    const active = revision;
    pending = true;
    button.disabled = true;
    status.textContent = "Проверяем запасы и цены для выбранных блюд…";
    try {
      const response = await fetch("/recipes/recommend", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          budget: { amount, currency: "KGS" },
          category: categories.value, limit: 6,
        }),
      });
      const data = await response.json().catch(() => ({ error: "invalid_response" }));
      if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      if (revision !== active) return;
      if (data.plan_saved !== false || data.inventory_updated !== false ||
          data.checkout_confirmed !== false) {
        throw new Error("Сервер не подтвердил режим безопасного предпросмотра.");
      }
      render(data);
    } catch (error) {
      if (revision === active) status.textContent = `Подбор не завершён: ${error.message}`;
    } finally {
      pending = false;
      button.disabled = false;
    }
  }
  for (const element of [categories, budget]) {
    element.addEventListener("input", invalidate);
    element.addEventListener("change", invalidate);
  }
  form.addEventListener("submit", suggest);
  window.HSMRecipeRecommendations = {
    configure(recipes) {
      const selected = categories.value;
      const choices = [...new Set((recipes || []).map(x => x.category))].sort();
      categories.replaceChildren();
      const all = document.createElement("option");
      all.value = "all";
      all.textContent = "Все категории";
      categories.appendChild(all);
      for (const category of choices) {
        const option = document.createElement("option");
        option.value = category;
        option.textContent = category;
        categories.appendChild(option);
      }
      categories.value = choices.includes(selected) ? selected : "all";
      invalidate();
    },
    invalidate,
  };
})();
