"use strict";

const state = {
  catalog: { items: [], skus: [] },
  household: null,
  reports: [],
  plans: [],
  history: [],
  activePlan: null,
  activeContext: null,
  mustHaves: new Map(),
  pendingStocktakes: new Map(),
  savingStocktakes: false,
  showAllStockItems: false,
  shoppingSession: null,
  shoppingConfirming: false,
  view: "shopping",
  usualBasketDraft: new Map(),
  usualBasketSkuChoices: new Map(),
  usualBasketAvailable: false,
  usualBasketDirty: false,
  usualBasketSaving: false,
  usualBasketPreviewId: null,
  usualBasketPreviewRevision: 0,
  recipes: [],
  recipeQuoteRevision: 0,
  usualBasketRepeatSettings: null,
  usualBasketRepeating: false,
};

class ApiError extends Error {
  constructor(status, payload) {
    super(payload?.detail || payload?.error || `Request failed (${status})`);
    this.status = status;
    this.payload = payload;
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body
      ? { "content-type": "application/json", ...(options.headers || {}) }
      : (options.headers || {}),
  });
  const payload = await response.json().catch(() => ({ error: "invalid_response" }));
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

function byId(id) { return document.getElementById(id); }
function itemById(id) { return state.catalog.items.find((item) => item.item_id === id); }
function skusForItem(id) { return state.catalog.skus.filter((sku) => sku.item_id === id); }
function skuById(id) { return state.catalog.skus.find((sku) => sku.sku_id === id) || null; }
function primarySku(id) { return skusForItem(id)[0] || null; }
function itemName(id) { return itemById(id)?.name || id; }
function balanceForItem(id) { return (state.household?.balances || []).find((entry) => entry.item_id === id) || null; }
function reportForItem(id) { return state.reports.find((entry) => entry.item_id === id) || null; }

function decimalText(value) {
  const raw = String(value);
  const match = raw.match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if (!match) return raw;
  const sign = match[1] === "-" ? "-" : "";
  const integer = match[2];
  const fraction = match[3] || "";
  const exponent = Number(match[4] || 0);
  const digits = integer + fraction;
  const point = integer.length + exponent;
  let plain;
  if (point <= 0) plain = `0.${"0".repeat(-point)}${digits}`;
  else if (point >= digits.length) plain = `${digits}${"0".repeat(point - digits.length)}`;
  else plain = `${digits.slice(0, point)}.${digits.slice(point)}`;
  if (plain.includes(".")) plain = plain.replace(/0+$/, "").replace(/\.$/, "");
  plain = plain.replace(/^0+(?=\d)/, "");
  if (plain.startsWith(".")) plain = `0${plain}`;
  if (!plain || plain === "-0") plain = "0";
  return sign && plain !== "0" ? `${sign}${plain}` : plain;
}

function scaleDecimalText(value, numerator, denominator = 1) {
  let plain = decimalText(value);
  let sign = "";
  if (plain.startsWith("-")) {
    sign = "-";
    plain = plain.slice(1);
  }
  const [integer, fraction = ""] = plain.split(".");
  let scale = fraction.length;
  let scaled = BigInt(`${integer || "0"}${fraction}` || "0") * BigInt(numerator);
  const divisor = BigInt(denominator);
  while (scaled % divisor !== 0n) {
    scaled *= 10n;
    scale += 1;
  }
  const quotient = (scaled / divisor).toString().padStart(scale + 1, "0");
  const result = scale === 0
    ? quotient
    : `${quotient.slice(0, -scale) || "0"}.${quotient.slice(-scale)}`;
  return `${sign}${decimalText(result)}`;
}

function normalizeNumberInput(value) {
  return String(value).trim().replace(/[\s\u00a0]+/g, "").replace(",", ".");
}

function displayNumber(value) {
  const plain = decimalText(value);
  const numeric = Number(plain);
  if (Number.isFinite(numeric) && Math.abs(numeric) < 1e15) {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(numeric);
  }
  return plain.replace(".", ",");
}

function unitLabel(unit) {
  const labels = {
    ml: "мл",
    l: "л",
    g: "г",
    kg: "кг",
    pcs: "шт.",
    piece: "шт.",
    pieces: "шт.",
    unit: "шт.",
  };
  return labels[unit] || unit;
}

function humanQuantity(quantity) {
  if (!quantity) return "—";
  let amount = Number(decimalText(quantity.amount));
  let unit = quantity.unit;
  if (Number.isFinite(amount)) {
    if (unit === "ml" && Math.abs(amount) >= 1000) {
      amount /= 1000;
      unit = "l";
    } else if (unit === "g" && Math.abs(amount) >= 1000) {
      amount /= 1000;
      unit = "kg";
    }
    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(amount)} ${unitLabel(unit)}`;
  }
  return `${displayNumber(quantity.amount)} ${unitLabel(unit)}`;
}

function moneyText(money) {
  if (!money) return "—";
  const currency = money.currency === "KGS" ? "сом" : money.currency;
  return `${displayNumber(money.amount)} ${currency}`;
}

function dayText(value) {
  const days = Number(value);
  if (!Number.isInteger(days)) return `${displayNumber(value)} дня`;
  const mod100 = days % 100;
  const mod10 = days % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${days} дней`;
  if (mod10 === 1) return `${days} день`;
  if (mod10 >= 2 && mod10 <= 4) return `${days} дня`;
  return `${days} дней`;
}

function packageText(count) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} упаковок`;
  if (mod10 === 1) return `${count} упаковка`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} упаковки`;
  return `${count} упаковок`;
}

function itemEmoji(itemId) {
  const known = { milk: "🥛", rice: "🍚", oil: "🫗", sunflower_oil: "🫗", pasta: "🍝", semolina: "🥣", canned_fish: "🐟", canned_peas: "🫛", seasoning: "🧂", eggs: "🥚", bread: "🍞", sugar: "🧊" };
  if (known[itemId]) return known[itemId];
  const category = itemById(itemId)?.category || "";
  if (category.includes("dairy")) return "🥛";
  if (category.includes("pantry") || category.includes("grain")) return "🥫";
  return "🧺";
}

function eventId(prefix) {
  const random = crypto.getRandomValues(new Uint32Array(1))[0].toString(36);
  return `${prefix}-${Date.now().toString(36)}-${random}`.toLowerCase();
}

function formEventId(form, prefix) {
  if (!form.dataset.pendingEventId) form.dataset.pendingEventId = eventId(prefix);
  return form.dataset.pendingEventId;
}

function clearFormEventId(form) {
  delete form.dataset.pendingEventId;
}

function elementEventId(element, prefix, operationKey) {
  if (element.dataset.pendingOperationKey !== operationKey) {
    element.dataset.pendingOperationKey = operationKey;
    element.dataset.pendingEventId = eventId(prefix);
  }
  return element.dataset.pendingEventId;
}

function clearElementEventId(element) {
  delete element.dataset.pendingOperationKey;
  delete element.dataset.pendingEventId;
}

function friendlyError(error) {
  if (!(error instanceof ApiError)) return error?.message || String(error);
  const code = error.payload?.error;
  if (code === "market_unavailable") return "Не получилось получить актуальные цены. Попробуйте ещё раз.";
  if (code === "household_state_conflict") return "В данных о запасах есть противоречие. Обновите остаток ещё раз.";
  if (code === "household_operation_conflict") return "Это изменение уже было записано по-другому. Обновите страницу и попробуйте снова.";
  if (code === "not_found") return "Не нашли нужную запись. Обновите страницу и попробуйте снова.";
  if (code === "storage_error" || code === "household_storage_error") return "Не удалось сохранить данные. Попробуйте ещё раз.";
  if (code === "invalid_request") {
    const detail = String(error.payload?.detail || "").toLowerCase();
    if (detail.includes("requires explicit") || detail.includes("recorded consumption")) {
      return "Пока недостаточно данных. Отметьте, что есть дома, или добавьте продукт в «Нужно обязательно».";
    }
    return "Проверьте введённые данные и попробуйте ещё раз.";
  }
  return "Что-то пошло не так. Попробуйте ещё раз.";
}

function showToast(message, error = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 4200);
}

function setConnection(online, text) {
  const node = byId("connection-status");
  node.classList.toggle("online", online);
  node.classList.toggle("offline", !online);
  node.lastElementChild.textContent = text;
}

function setView(name) {
  state.view = name;
  for (const button of document.querySelectorAll("[data-view]")) {
    button.classList.toggle("active", button.dataset.view === name);
  }
  for (const panel of document.querySelectorAll("[data-view-panel]")) {
    panel.classList.toggle("active", panel.dataset.viewPanel === name);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function appendOption(select, value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  select.appendChild(option);
}

function refillSkuSelect(select) {
  const current = select.value;
  select.replaceChildren();
  for (const sku of state.catalog.skus) {
    appendOption(select, sku.sku_id, `${sku.name} · ${humanQuantity(sku.package_quantity)}`);
  }
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function renderFirstRun() {
  byId("first-run-card").classList.toggle("hidden", state.history.length > 0);
}

function renderProductPicker() {
  const picker = byId("product-picker");
  const query = byId("product-search").value.trim().toLocaleLowerCase("ru-RU");
  picker.replaceChildren();
  const items = state.catalog.items.filter((item) => {
    const text = `${item.name} ${(item.aliases || []).join(" ")}`.toLocaleLowerCase("ru-RU");
    return !query || text.includes(query);
  });

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Ничего не нашли.";
    picker.appendChild(empty);
    return;
  }

  for (const item of items) {
    const sku = primarySku(item.item_id);
    if (!sku) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "product-option";
    const emoji = document.createElement("span");
    emoji.className = "product-emoji";
    emoji.textContent = itemEmoji(item.item_id);
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = item.name;
    const note = document.createElement("small");
    note.textContent = `${humanQuantity(sku.package_quantity)} · добавить`;
    copy.append(title, note);
    button.append(emoji, copy);
    button.addEventListener("click", () => {
      state.mustHaves.set(item.item_id, (state.mustHaves.get(item.item_id) || 0) + 1);
      renderMustHaves();
    });
    picker.appendChild(button);
  }
}

function renderMustHaves() {
  const list = byId("must-have-list");
  list.replaceChildren();
  for (const [itemId, count] of [...state.mustHaves.entries()].sort()) {
    const sku = primarySku(itemId);
    if (!sku) continue;
    const row = document.createElement("div");
    row.className = "need-choice";

    const copy = document.createElement("div");
    const title = document.createElement("div");
    title.className = "need-choice-title";
    const emoji = document.createElement("span");
    emoji.textContent = itemEmoji(itemId);
    const name = document.createElement("strong");
    name.textContent = itemName(itemId);
    title.append(emoji, name);
    const note = document.createElement("small");
    note.textContent = `По ${humanQuantity(sku.package_quantity)} в упаковке`;
    copy.append(title, note);

    const stepper = makeStepper(count, (next) => {
      if (next <= 0) state.mustHaves.delete(itemId);
      else state.mustHaves.set(itemId, next);
      renderMustHaves();
    });

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button danger";
    remove.textContent = "Убрать";
    remove.addEventListener("click", () => {
      state.mustHaves.delete(itemId);
      renderMustHaves();
    });

    row.append(copy, stepper, remove);
    list.appendChild(row);
  }
}

function makeStepper(value, onChange, minimum = 0) {
  const stepper = document.createElement("div");
  stepper.className = "stepper";
  const minus = document.createElement("button");
  minus.type = "button";
  minus.className = "stepper-button";
  minus.textContent = "−";
  minus.setAttribute("aria-label", "Уменьшить");
  const current = document.createElement("span");
  current.className = "stepper-value";
  current.textContent = String(value);
  const plus = document.createElement("button");
  plus.type = "button";
  plus.className = "stepper-button";
  plus.textContent = "+";
  plus.setAttribute("aria-label", "Увеличить");
  minus.addEventListener("click", () => onChange(Math.max(minimum, value - 1)));
  plus.addEventListener("click", () => onChange(value + 1));
  stepper.append(minus, current, plus);
  return stepper;
}

function collectMustHaves() {
  const needs = [];
  for (const [itemId, count] of state.mustHaves.entries()) {
    const sku = primarySku(itemId);
    if (!sku || count <= 0) continue;
    needs.push({
      item_id: itemId,
      quantity: {
        amount: scaleDecimalText(sku.package_quantity.amount, count),
        unit: sku.package_quantity.unit,
      },
    });
  }
  return needs;
}

function sameQuantity(left, right) {
  if (!left || !right || left.unit !== right.unit) return false;
  return decimalText(left.amount) === decimalText(right.amount);
}

function queueStocktake(itemId, quantity) {
  const balance = balanceForItem(itemId);
  if (balance && sameQuantity(balance.quantity, quantity)) {
    state.pendingStocktakes.delete(itemId);
  } else {
    state.pendingStocktakes.set(itemId, {
      quantity,
      eventId: null,
    });
  }
  renderHome();
}

function renderStocktakeActions() {
  const actions = byId("stocktake-actions");
  const count = state.pendingStocktakes.size;
  actions.classList.toggle("hidden", count === 0);
  byId("stocktake-pending-label").textContent = `Изменено товаров: ${count}`;

  const discard = byId("discard-pending-stocktakes");
  const save = byId("save-pending-stocktakes");
  discard.disabled = state.savingStocktakes;
  save.disabled = state.savingStocktakes || count === 0;
  save.textContent = state.savingStocktakes
    ? "Сохраняем…"
    : `Сохранить изменения (${count})`;
}

function confirmedStockItemIds() {
  // Only confirmed household evidence activates focus. Pending updates must
  // not collapse a first-time user's full catalog during their first batch.
  const confirmed = new Set(
    (state.household?.balances || []).map((balance) => balance.item_id),
  );
  for (const event of state.history) {
    if (["inventory_correction", "purchase"].includes(event.event_type) && event.item?.id) {
      confirmed.add(event.item.id);
    }
  }
  return confirmed;
}

function trackedStockItemIds(confirmed = confirmedStockItemIds()) {
  const tracked = new Set(confirmed);
  // Keep unsaved selections visible inside an already focused household.
  for (const itemId of state.pendingStocktakes.keys()) tracked.add(itemId);
  return tracked;
}

// These are verification prompts, not inferred quantities or household facts.
// Use the household server's as_of timestamp rather than an untrusted device clock.
function stockVerificationHints(asOf, history, reports, confirmedIds) {
  const now = Date.parse(asOf || "");
  const hints = new Map();
  if (!Number.isFinite(now)) return hints;

  const latestChecks = new Map();
  for (const event of history) {
    if (event.event_type !== "inventory_correction" || !event.item?.id) continue;
    const timestamp = Date.parse(event.body?.occurred_at || "");
    if (!Number.isFinite(timestamp) || timestamp > now) continue;
    const itemId = event.item.id;
    if (timestamp > (latestChecks.get(itemId) ?? -Infinity)) {
      latestChecks.set(itemId, timestamp);
    }
  }

  const admittedRateIds = new Set(
    reports.filter((report) =>
      report.estimate && report.recurring_admission?.status === "accepted"
    ).map((report) => report.item_id),
  );

  for (const itemId of confirmedIds) {
    const lastCheck = latestChecks.get(itemId);
    if (lastCheck === undefined) {
      // This household has purchase evidence but no actual stocktake.
      hints.set(itemId, {
        priority: 0,
        description: "Ещё не сверяли остаток — стоит проверить",
      });
      continue;
    }
    const days = Math.floor((now - lastCheck) / 86400000);
    if (days >= 7 && admittedRateIds.has(itemId)) {
      hints.set(itemId, {
        priority: 1,
        description: `Проверяли ${days} дн. назад · есть история расхода`,
      });
    } else if (days >= 14) {
      hints.set(itemId, {
        priority: 2,
        description: `Проверяли ${days} дн. назад`,
      });
    }
  }
  return hints;
}

function renderHome() {
  const container = byId("home-items");
  container.replaceChildren();

  const allItems = state.catalog.items.filter((item) => primarySku(item.item_id));
  const confirmed = confirmedStockItemIds();
  const tracked = trackedStockItemIds(confirmed);
  const trackedCount = allItems.filter((item) => tracked.has(item.item_id)).length;
  const confirmedCount = allItems.filter((item) => confirmed.has(item.item_id)).length;
  const canFocus = confirmedCount > 0 && confirmedCount < allItems.length;
  const showingAll = !canFocus || state.showAllStockItems;
  const hints = stockVerificationHints(
    state.household?.as_of, state.history, state.reports, confirmed,
  );
  const toggle = byId("home-filter-toggle");
  toggle.classList.toggle("hidden", !canFocus);
  toggle.disabled = state.savingStocktakes;
  toggle.setAttribute("aria-pressed", String(showingAll));
  toggle.textContent = showingAll
    ? `Только мои товары (${trackedCount})`
    : `Все товары (${allItems.length})`;
  byId("home-filter-summary").textContent = showingAll
    ? `Показаны все товары: ${allItems.length}`
    : `Ваши товары: ${trackedCount} из ${allItems.length}`;

  const visibleItems = showingAll
    ? allItems
    : allItems.filter((item) => tracked.has(item.item_id));
  // Stable sort preserves catalog order for equal priorities. Pending selections
  // do not change rank, so cards do not jump while the user is marking a batch.
  visibleItems.sort((left, right) =>
    (hints.get(left.item_id)?.priority ?? 9) -
    (hints.get(right.item_id)?.priority ?? 9)
  );
  const suggestedCount = visibleItems.filter((item) =>
    hints.has(item.item_id) && !state.pendingStocktakes.has(item.item_id)
  ).length;
  const summary = byId("home-check-summary");
  summary.classList.toggle("hidden", suggestedCount === 0);
  summary.textContent = suggestedCount
    ? `Стоит проверить: ${suggestedCount}. Подсказки не меняют запасы.`
    : "";
  for (const item of visibleItems) {
    const sku = primarySku(item.item_id);
    if (!sku) continue;
    const balance = balanceForItem(item.item_id);
    const pending = state.pendingStocktakes.get(item.item_id) || null;

    const card = document.createElement("article");
    card.className = "home-card";
    card.classList.toggle("pending", Boolean(pending));
    const top = document.createElement("div");
    top.className = "home-card-top";

    const titleBlock = document.createElement("div");
    titleBlock.className = "home-card-title";
    const emoji = document.createElement("span");
    emoji.className = "product-emoji";
    emoji.textContent = itemEmoji(item.item_id);
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = item.name;
    const pack = document.createElement("small");
    pack.textContent = `1 уп. = ${humanQuantity(sku.package_quantity)}`;
    copy.append(name, pack);
    titleBlock.append(emoji, copy);

    const current = document.createElement("div");
    current.className = "home-current";
    current.classList.toggle("pending", Boolean(pending));
    if (pending) {
      current.textContent = `Будет ${humanQuantity(pending.quantity)}`;
    } else {
      current.textContent = balance
        ? (Number(balance.quantity.amount) === 0 ? "Нет дома" : humanQuantity(balance.quantity))
        : "Не отмечено";
    }
    top.append(titleBlock, current);
    card.appendChild(top);

    const hint = hints.get(item.item_id);
    if (hint && !pending) {
      const reminder = document.createElement("p");
      reminder.className = "stock-verification-hint";
      reminder.textContent = hint.description;
      card.appendChild(reminder);
    }

    const choices = document.createElement("div");
    choices.className = "quick-stocktake";
    const countUnit = ["pcs", "piece", "pieces", "unit"].includes(sku.package_quantity.unit);
    const presets = countUnit
      ? [
          ["Нет", 0, 1, "нет дома"],
          ["1", 1, 1, "1 упаковка"],
          ["2", 2, 1, "2 упаковки"],
          ["5", 5, 1, "5 упаковок"],
        ]
      : [
          ["Нет", 0, 1, "нет дома"],
          ["½", 1, 2, "половина упаковки"],
          ["1", 1, 1, "1 упаковка"],
          ["2", 2, 1, "2 упаковки"],
        ];

    let pendingMatchesPreset = false;
    for (const [label, numerator, denominator, accessibleLabel] of presets) {
      const amount = scaleDecimalText(sku.package_quantity.amount, numerator, denominator);
      const quantity = { amount, unit: sku.package_quantity.unit };
      const selected = Boolean(pending && sameQuantity(pending.quantity, quantity));
      pendingMatchesPreset ||= selected;

      const button = document.createElement("button");
      button.type = "button";
      button.className = "stock-choice";
      button.classList.toggle("selected", selected);
      button.textContent = label;
      button.disabled = state.savingStocktakes;
      button.setAttribute("aria-pressed", selected ? "true" : "false");
      button.setAttribute("aria-label", `${item.name}: ${accessibleLabel}`);
      button.addEventListener("click", () => queueStocktake(item.item_id, quantity));
      choices.appendChild(button);
    }

    const other = document.createElement("button");
    other.type = "button";
    other.className = "stock-choice";
    other.classList.toggle("selected", Boolean(pending) && !pendingMatchesPreset);
    other.textContent = "…";
    other.title = "Другое количество";
    other.disabled = state.savingStocktakes;
    other.setAttribute("aria-label", `${item.name}: другое количество`);
    choices.appendChild(other);
    card.appendChild(choices);

    const custom = document.createElement("form");
    custom.className = "custom-stocktake";
    const label = document.createElement("label");
    const labelText = document.createElement("span");
    labelText.textContent = "Точный остаток";
    const input = document.createElement("input");
    input.inputMode = "decimal";
    input.autocomplete = "off";
    input.placeholder = "например, 0,7";
    input.required = true;
    input.disabled = state.savingStocktakes;
    label.append(labelText, input);
    const unit = document.createElement("span");
    unit.className = "custom-unit";
    unit.textContent = unitLabel(sku.package_quantity.unit);
    const choose = document.createElement("button");
    choose.type = "submit";
    choose.className = "secondary-button";
    choose.textContent = "Выбрать";
    choose.disabled = state.savingStocktakes;
    custom.append(label, unit, choose);
    other.addEventListener("click", () => {
      custom.classList.toggle("visible");
      other.setAttribute("aria-expanded", custom.classList.contains("visible") ? "true" : "false");
      if (custom.classList.contains("visible")) input.focus();
    });
    custom.addEventListener("submit", (event) => {
      event.preventDefault();
      const amount = normalizeNumberInput(input.value);
      if (!amount) return;
      queueStocktake(item.item_id, { amount, unit: sku.package_quantity.unit });
    });
    card.appendChild(custom);
    container.appendChild(card);
  }
  renderStocktakeActions();
}

async function savePendingStocktakes() {
  if (state.savingStocktakes || state.pendingStocktakes.size === 0) return;

  state.savingStocktakes = true;
  renderHome();

  let saved = 0;
  let failure = null;
  const entries = [...state.pendingStocktakes.entries()];

  for (const [itemId, pending] of entries) {
    if (!pending.eventId) pending.eventId = eventId("stocktake");
    try {
      const response = await request("/household/stocktakes", {
        method: "POST",
        body: JSON.stringify({
          event_id: pending.eventId,
          item_id: itemId,
          quantity: pending.quantity,
          reason: "browser rapid stock update",
        }),
      });
      state.pendingStocktakes.delete(itemId);
      if (response.household) state.household = response.household;
      saved += 1;
    } catch (error) {
      failure = error;
      break;
    }
  }

  state.savingStocktakes = false;
  try {
    await refreshOperationalState();
  } catch (error) {
    failure ||= error;
    renderHome();
  }

  if (failure) {
    const progress = saved ? `Сохранено изменений: ${saved}. ` : "";
    showToast(`${progress}${friendlyError(failure)}`, true);
  } else {
    showToast(`Запасы обновлены. Сохранено изменений: ${saved}.`);
  }
}

function renderLearning() {
  const list = byId("learning-list");
  list.replaceChildren();
  if (!state.reports.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Пока мало данных. Просто обновляйте остатки время от времени.";
    list.appendChild(empty);
    return;
  }
  for (const report of state.reports) {
    const card = document.createElement("article");
    card.className = "stack-card";
    const row = document.createElement("div");
    row.className = "stack-card-row";
    const name = document.createElement("strong");
    name.textContent = `${itemEmoji(report.item_id)} ${itemName(report.item_id)}`;
    const rate = document.createElement("span");
    const recurringReady = report?.recurring_admission?.status === "accepted";
    rate.textContent = report.estimate && recurringReady
      ? `${humanQuantity(report.estimate.daily_quantity)} / день`
      : "Нужно больше данных";
    row.append(name, rate);
    const note = document.createElement("small");
    note.textContent = report.estimate && recurringReady
      ? `Учтены наблюдения примерно за ${displayNumber(report.estimate.observed_days)} дн.`
      : report.estimate
        ? "Есть предварительная оценка, но она пока не влияет на будущие покупки."
        : "После следующих обновлений остатка система попробует оценить обычный расход.";
    card.append(row, note);
    list.appendChild(card);
  }
}

function renderRecentPlans() {
  const list = byId("recent-plans");
  list.replaceChildren();
  if (!state.plans.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Здесь появятся составленные списки покупок.";
    list.appendChild(empty);
    return;
  }
  for (const plan of state.plans) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "stack-card plan-history-button";
    const row = document.createElement("div");
    row.className = "stack-card-row";
    const date = document.createElement("strong");
    date.textContent = new Date(plan.created_at).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
    const cost = document.createElement("span");
    cost.textContent = moneyText(plan.total_cost);
    row.append(date, cost);
    const note = document.createElement("small");
    note.textContent = plan.status === "feasible" ? "Список был составлен" : "Не удалось подобрать покупки";
    button.append(row, note);
    button.addEventListener("click", async () => {
      await openStoredPlan(plan.plan_id);
      setView("shopping");
    });
    list.appendChild(button);
  }
}

function eventDescription(event) {
  const body = event.body || {};
  if (event.event_type === "inventory_correction") {
    return `${itemName(event.item.id)}: осталось ${humanQuantity(body.quantity_on_hand)}`;
  }
  if (event.event_type === "purchase") {
    return `Купили ${itemName(event.item.id)} — ${humanQuantity(body.quantity)}`;
  }
  if (event.event_type === "consumption_observation") {
    return `${itemName(event.item.id)}: учтён расход ${humanQuantity(body.quantity_consumed)}`;
  }
  return itemName(event.item?.id || event.event_id);
}

function renderActivity() {
  const list = byId("activity-list");
  list.replaceChildren();
  const events = [...state.history].reverse().slice(0, 16);
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Пока ничего не менялось.";
    list.appendChild(empty);
    return;
  }
  for (const event of events) {
    const row = document.createElement("div");
    row.className = "activity-row";
    const time = document.createElement("span");
    time.className = "activity-time";
    time.textContent = new Date(event.recorded_at).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
    const description = document.createElement("strong");
    description.className = "activity-description";
    description.textContent = eventDescription(event);
    row.append(time, description);
    list.appendChild(row);
  }
}

function addChip(container, label, value) {
  const chip = document.createElement("span");
  chip.className = "summary-chip";
  chip.append(document.createTextNode(label));
  const strong = document.createElement("strong");
  strong.textContent = value;
  chip.appendChild(strong);
  container.appendChild(chip);
}

function explanationItem(titleText, detailText) {
  const item = document.createElement("article");
  item.className = "explanation-item";
  const title = document.createElement("strong");
  title.textContent = titleText;
  const detail = document.createElement("small");
  detail.textContent = detailText;
  item.append(title, detail);
  return item;
}

function positiveQuantity(quantity) {
  if (!quantity) return false;
  const amount = Number(decimalText(quantity.amount));
  return Number.isFinite(amount) && amount > 0;
}

function buildCoverageReason(record, context) {
  const result = record.result || {};
  const requestData = record.request || {};
  const basis =
    requestData.decision_basis?.kind === "household_replenishment"
      ? requestData.decision_basis
      : null;
  const demands = new Map(
    (requestData.demands || []).map((entry) => [entry.item_id, entry.quantity]),
  );
  const explicitByItem = new Map(
    (basis?.explicit_needs || []).map((entry) => [entry.item_id, entry]),
  );
  const recurringByItem = new Map(
    (basis?.recurring_estimates || []).map((entry) => [entry.item_id, entry]),
  );
  const recurringContributionByItem = new Map(
    (basis?.contributions || [])
      .filter((entry) => entry.source_id === "household:recurring")
      .map((entry) => [entry.item_id, entry]),
  );
  const nodes = [];

  if (basis) {
    const factors = [];
    if (requestData.budget) factors.push(`бюджет ${moneyText(requestData.budget)}`);
    if (recurringByItem.size) {
      factors.push(`период ${dayText(basis.horizon_days)}`);
      factors.push("история расхода с достаточным количеством наблюдений");
    }
    if (explicitByItem.size) factors.push("обязательные покупки");
    if ((requestData.inventory || []).length) factors.push("запасы дома");
    if (requestData.objective) factors.push("правила выбора между вариантами");
    if (result.market?.offer_count) factors.push("доступные цены и упаковки");
    nodes.push(
      explanationItem(
        "На что опирался план",
        factors.length
          ? `Учтены: ${factors.join(", ")}.`
          : "Сохранена точная основа этого расчёта.",
      ),
    );
  } else if (result.status === "feasible") {
    nodes.push(
      explanationItem(
        "Старый сохранённый план",
        "Для этого плана ещё не сохранялась расширенная история причин. Ниже показана только сохранённая арифметика расчёта.",
      ),
    );
  }

  for (const coverage of result.coverage || []) {
    const demand = demands.get(coverage.item_id) || coverage.required;
    const explicit = explicitByItem.get(coverage.item_id);
    const recurring = recurringByItem.get(coverage.item_id);
    const recurringContribution = recurringContributionByItem.get(coverage.item_id);

    if (!basis) {
      nodes.push(
        explanationItem(
          `${itemEmoji(coverage.item_id)} ${itemName(coverage.item_id)}`,
          `Нужно ${humanQuantity(demand)} · дома учтено ${humanQuantity(coverage.inventory_used)} · покупкой добавим ${humanQuantity(coverage.purchased)}.`,
        ),
      );
      continue;
    }

    const details = [];
    if (explicit) {
      details.push(`Вы добавили обязательно ${humanQuantity(explicit.quantity)}.`);
    }
    if (recurring) {
      const contribution =
        recurringContribution?.quantity || recurring.contribution_quantity;
      let recurringText =
        `Обычный расход ≈ ${humanQuantity(recurring.daily_quantity)} в день`;
      if (recurring.observed_days) {
        recurringText +=
          `, по наблюдениям примерно за ${displayNumber(recurring.observed_days)} дн.`;
      } else {
        recurringText += ".";
      }
      if (contribution) {
        recurringText +=
          ` На ${dayText(basis.horizon_days)} из этого учтено ${humanQuantity(contribution)}.`;
      }
      details.push(recurringText);
    }
    if (positiveQuantity(coverage.inventory_used)) {
      details.push(
        `Из запасов дома покрывается ${humanQuantity(coverage.inventory_used)}.`,
      );
    }
    details.push(`Итого нужно ${humanQuantity(demand)}.`);
    details.push(
      positiveQuantity(coverage.purchased)
        ? `Покупкой добавим ${humanQuantity(coverage.purchased)}.`
        : "Дополнительно покупать этот продукт не нужно.",
    );

    nodes.push(
      explanationItem(
        `${itemEmoji(coverage.item_id)} ${itemName(coverage.item_id)}`,
        details.join(" "),
      ),
    );
  }

  if (!(result.coverage || []).length && result.status !== "feasible") {
    const reasons = result.infeasibility_reasons || [];
    const explanations = result.explanation || [];
    let detail = "Попробуйте увеличить бюджет или изменить обязательные продукты.";
    if (result.minimum_required_cost) {
      detail =
        `Для выполнения всех потребностей нужно минимум около ${moneyText(result.minimum_required_cost)}.`;
    } else if (reasons.length) {
      detail = reasons.join(" ");
    } else if (explanations.length) {
      detail = explanations.join(" ");
    }
    nodes.push(explanationItem("Не удалось составить план", detail));
  }

  return nodes;
}

const SHOPPING_STATUS = Object.freeze({
  PENDING: "pending",
  PICKED: "picked",
  NOT_FOUND: "not_found",
  SKIPPED: "skipped",
  CONFIRMED: "confirmed",
});

function shoppingStorageKey(planId) {
  return `hsp:shopping:${planId}`;
}

function shoppingEntryKey(purchase, index) {
  return `${purchase.offer_id || purchase.sku_id}:${index}`;
}

function confirmedPlanEvents(planId) {
  const sourceRef = `plan:${planId}`;
  return state.history.filter(
    (event) =>
      event.event_type === "purchase" &&
      event.body?.source_ref === sourceRef &&
      typeof event.event_id === "string" &&
      typeof event.body?.sku_id === "string",
  );
}

function loadShoppingSession(record) {
  const purchases = record.result?.purchases || [];
  const confirmedEvents = confirmedPlanEvents(record.plan_id);
  const confirmedById = new Map(
    confirmedEvents.map((event) => [event.event_id, event]),
  );
  let stored = null;
  try {
    stored = JSON.parse(sessionStorage.getItem(shoppingStorageKey(record.plan_id)) || "null");
  } catch {
    stored = null;
  }

  const validStatuses = new Set(Object.values(SHOPPING_STATUS));
  const storedCurrent =
    stored &&
    stored.planId === record.plan_id &&
    stored.items &&
    typeof stored.items === "object";

  const reservedEventIds = new Set();
  if (storedCurrent) {
    for (const previous of Object.values(stored.items)) {
      if (
        previous &&
        typeof previous.eventId === "string" &&
        confirmedById.has(previous.eventId)
      ) {
        reservedEventIds.add(previous.eventId);
      }
    }
  }

  const legacyEventsBySku = new Map();
  if (!storedCurrent) {
    for (const event of confirmedEvents) {
      if (reservedEventIds.has(event.event_id)) continue;
      const skuId = event.body.sku_id;
      const existing = legacyEventsBySku.get(skuId) || [];
      existing.push(event);
      legacyEventsBySku.set(skuId, existing);
    }
  }

  const items = {};
  purchases.forEach((purchase, index) => {
    const key = shoppingEntryKey(purchase, index);
    const previous =
      storedCurrent && typeof stored.items[key] === "object"
        ? stored.items[key]
        : null;

    let eventId =
      previous && typeof previous.eventId === "string" && previous.eventId
        ? previous.eventId
        : null;
    let status =
      previous && validStatuses.has(previous.status)
        ? previous.status
        : SHOPPING_STATUS.PENDING;

    if (eventId && confirmedById.has(eventId)) {
      status = SHOPPING_STATUS.CONFIRMED;
    } else if (status === SHOPPING_STATUS.CONFIRMED) {
      // A reset can invalidate stale session-only confirmation state.
      status = SHOPPING_STATUS.PENDING;
      eventId = null;
    }

    if (!eventId && !storedCurrent) {
      const legacy = legacyEventsBySku.get(purchase.sku_id) || [];
      const recovered = legacy.shift() || null;
      if (recovered) {
        eventId = recovered.event_id;
        status = SHOPPING_STATUS.CONFIRMED;
      }
    }

    const actualPacks =
      previous && Number.isInteger(previous.actualPacks) && previous.actualPacks > 0
        ? previous.actualPacks
        : purchase.packs;

    items[key] = {
      status,
      actualPacks,
      eventId,
    };
  });

  const completed =
    Boolean(stored?.completed) &&
    Object.values(items).every(
      (entry) =>
        entry.status === SHOPPING_STATUS.CONFIRMED ||
        entry.status === SHOPPING_STATUS.NOT_FOUND ||
        entry.status === SHOPPING_STATUS.SKIPPED,
    );

  return { planId: record.plan_id, items, completed };
}

function ensureShoppingSession(record) {
  if (!record.plan_id) return null;
  if (!state.shoppingSession || state.shoppingSession.planId !== record.plan_id) {
    state.shoppingSession = loadShoppingSession(record);
    persistShoppingSession();
  }
  return state.shoppingSession;
}

function persistShoppingSession() {
  const session = state.shoppingSession;
  if (!session?.planId) return;
  sessionStorage.setItem(shoppingStorageKey(session.planId), JSON.stringify(session));
}

function shoppingCounts(session) {
  const entries = Object.values(session?.items || {});
  return {
    total: entries.length,
    resolved: entries.filter((entry) => entry.status !== SHOPPING_STATUS.PENDING).length,
    picked: entries.filter((entry) => entry.status === SHOPPING_STATUS.PICKED).length,
    confirmed: entries.filter((entry) => entry.status === SHOPPING_STATUS.CONFIRMED).length,
  };
}

function rerenderShopping(record) {
  renderPlan(record, state.activeContext, { preserveScroll: true });
}

function setShoppingStatus(record, key, status) {
  const session = ensureShoppingSession(record);
  const entry = session?.items?.[key];
  if (
    !entry ||
    session.completed ||
    state.shoppingConfirming ||
    entry.status === SHOPPING_STATUS.CONFIRMED
  ) return;

  entry.status = entry.status === status ? SHOPPING_STATUS.PENDING : status;
  persistShoppingSession();
  rerenderShopping(record);
}

function setShoppingPacks(record, key, packs) {
  const session = ensureShoppingSession(record);
  const entry = session?.items?.[key];
  if (
    !entry ||
    session.completed ||
    state.shoppingConfirming ||
    entry.status !== SHOPPING_STATUS.PICKED
  ) return;

  entry.actualPacks = Math.max(1, packs);
  persistShoppingSession();
  rerenderShopping(record);
}

function renderShoppingActions(record, feasible) {
  const actions = byId("shopping-actions");
  const purchases = record.result?.purchases || [];
  if (!feasible || !purchases.length) {
    actions.classList.add("hidden");
    return;
  }

  const session = ensureShoppingSession(record);
  const counts = shoppingCounts(session);
  const label = byId("shopping-progress-label");
  const note = byId("shopping-progress-note");
  const finish = byId("finish-shopping");

  actions.classList.remove("hidden");
  if (session.completed) {
    label.textContent = "Покупки завершены";
    note.textContent =
      counts.confirmed > 0
        ? `Подтверждено покупок: ${counts.confirmed}.`
        : "По этому списку фактических покупок не было.";
    finish.textContent = "Готово";
    finish.disabled = true;
    return;
  }

  label.textContent = `${counts.resolved} из ${counts.total} отмечено`;
  const missing = counts.total - counts.resolved;
  if (missing > 0) {
    note.textContent = `Осталось решить по позициям: ${missing}.`;
    finish.textContent = `Осталось отметить: ${missing}`;
    finish.disabled = true;
    return;
  }

  note.textContent =
    counts.picked > 0
      ? "Домашние запасы изменятся только после этого подтверждения."
      : "Все позиции отмечены без фактической покупки.";
  finish.textContent = state.shoppingConfirming
    ? "Подтверждаем…"
    : counts.picked > 0
      ? `Подтвердить покупки (${counts.picked})`
      : "Завершить без покупок";
  finish.disabled = state.shoppingConfirming;
}

async function finishShoppingSession(record) {
  const session = ensureShoppingSession(record);
  if (!session || session.completed || state.shoppingConfirming) return;

  const counts = shoppingCounts(session);
  if (counts.resolved !== counts.total) return;

  const purchases = record.result?.purchases || [];
  const pending = purchases
    .map((purchase, index) => ({ purchase, key: shoppingEntryKey(purchase, index) }))
    .filter(({ key }) => session.items[key]?.status === SHOPPING_STATUS.PICKED);

  if (!pending.length) {
    session.completed = true;
    persistShoppingSession();
    rerenderShopping(record);
    showToast("Покупки завершены. Домашние запасы не изменились.");
    return;
  }

  state.shoppingConfirming = true;
  rerenderShopping(record);

  let saved = 0;
  let failure = null;
  for (const { purchase, key } of pending) {
    const entry = session.items[key];
    if (!entry.eventId) {
      entry.eventId = eventId("purchase");
      persistShoppingSession();
    }
    try {
      const response = await request(
        `/plans/${encodeURIComponent(record.plan_id)}/purchases`,
        {
          method: "POST",
          body: JSON.stringify({
            event_id: entry.eventId,
            sku_id: purchase.sku_id,
            packs: entry.actualPacks,
          }),
        },
      );
      entry.status = SHOPPING_STATUS.CONFIRMED;
      if (response.household) state.household = response.household;
      saved += 1;
      persistShoppingSession();
    } catch (error) {
      failure = error;
      break;
    }
  }

  state.shoppingConfirming = false;
  const remaining = shoppingCounts(session);
  if (!failure && remaining.picked === 0 && remaining.resolved === remaining.total) {
    session.completed = true;
  }
  persistShoppingSession();

  if (saved > 0) {
    try {
      await refreshOperationalState();
    } catch (error) {
      failure ||= error;
    }
  }

  rerenderShopping(record);
  if (failure) {
    const progress = saved ? `Подтверждено покупок: ${saved}. ` : "";
    showToast(`${progress}${friendlyError(failure)}`, true);
  } else {
    showToast(`Покупки подтверждены: ${saved}. Домашние запасы обновлены.`);
  }
}

function renderPlan(record, context = null, options = {}) {
  const preservedScroll = options.preserveScroll ? window.scrollY : null;
  state.activePlan = record;
  state.activeContext = context;
  const panel = byId("plan-result-panel");
  panel.classList.remove("hidden");

  const result = record.result || {};
  const feasible = result.status === "feasible";
  const status = byId("plan-status");
  status.textContent = feasible ? "Можно купить" : "Нужно изменить";
  status.classList.toggle("infeasible", !feasible);

  const summary = byId("plan-summary");
  summary.replaceChildren();
  addChip(summary, "Итого", moneyText(result.total_cost));
  addChip(summary, "Останется", moneyText(result.budget_remaining));
  if (context?.demand?.horizon_days) addChip(summary, "На", dayText(context.demand.horizon_days));
  if ((result.purchases || []).length) addChip(summary, "Позиций", String(result.purchases.length));

  const purchases = byId("purchase-list");
  purchases.replaceChildren();

  if (!(result.purchases || []).length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";

    if (feasible) {
      empty.textContent = "Похоже, сейчас ничего докупать не нужно.";
    } else {
      const reasons = result.infeasibility_reasons || [];
      const explanations = result.explanation || [];
      if (result.minimum_required_cost) {
        empty.textContent =
          `С этим бюджетом план не помещается. Нужно минимум около ${moneyText(result.minimum_required_cost)}.`;
      } else if (reasons.length) {
        empty.textContent = reasons.join(" ");
      } else if (explanations.length) {
        empty.textContent = explanations.join(" ");
      } else {
        empty.textContent =
          "Не удалось составить план. Попробуйте увеличить бюджет или изменить обязательные покупки.";
      }
    }
    purchases.appendChild(empty);
  }

  const session =
    feasible && (result.purchases || []).length ? ensureShoppingSession(record) : null;

  for (const [index, purchase] of (result.purchases || []).entries()) {
    const key = shoppingEntryKey(purchase, index);
    const entry = session?.items?.[key] || {
      status: SHOPPING_STATUS.PENDING,
      actualPacks: purchase.packs,
      eventId: null,
    };
    const sku = skuById(purchase.sku_id);

    const card = document.createElement("article");
    card.className = "purchase-card shopping-card";
    card.classList.toggle("picked", entry.status === SHOPPING_STATUS.PICKED);
    card.classList.toggle("not-found", entry.status === SHOPPING_STATUS.NOT_FOUND);
    card.classList.toggle("skipped", entry.status === SHOPPING_STATUS.SKIPPED);
    card.classList.toggle("confirmed", entry.status === SHOPPING_STATUS.CONFIRMED);

    const main = document.createElement("div");
    main.className = "purchase-main";
    const left = document.createElement("div");
    left.className = "purchase-left";
    const emoji = document.createElement("span");
    emoji.className = "product-emoji";
    emoji.textContent = itemEmoji(purchase.item_id);
    const copy = document.createElement("div");
    const title = document.createElement("div");
    title.className = "purchase-title";
    title.textContent = itemName(purchase.item_id);
    const meta = document.createElement("div");
    meta.className = "purchase-meta";
    const packageSize = sku?.package_quantity
      ? humanQuantity(sku.package_quantity)
      : humanQuantity(purchase.acquired_quantity);
    meta.textContent = `${purchase.sku_name || purchase.sku_id} · ${packageSize}`;
    const planned = document.createElement("div");
    planned.className = "purchase-plan";
    planned.textContent = `План: ${packageText(purchase.packs)} · ${moneyText(purchase.cost)}`;
    copy.append(title, meta, planned);
    left.append(emoji, copy);
    main.appendChild(left);

    if (entry.status === SHOPPING_STATUS.CONFIRMED) {
      const badge = document.createElement("span");
      badge.className = "shopping-state-badge confirmed";
      badge.textContent = "✓ Подтверждено";
      main.appendChild(badge);
    } else if (entry.status === SHOPPING_STATUS.NOT_FOUND) {
      const badge = document.createElement("span");
      badge.className = "shopping-state-badge not-found";
      badge.textContent = "Не найден";
      main.appendChild(badge);
    } else if (entry.status === SHOPPING_STATUS.SKIPPED) {
      const badge = document.createElement("span");
      badge.className = "shopping-state-badge skipped";
      badge.textContent = "Не беру";
      main.appendChild(badge);
    } else {
      const cost = document.createElement("div");
      cost.className = "purchase-cost";
      cost.textContent = moneyText(purchase.cost);
      main.appendChild(cost);
    }
    card.appendChild(main);

    if (entry.status === SHOPPING_STATUS.CONFIRMED) {
      const done = document.createElement("div");
      done.className = "shopping-confirmed-note";
      done.textContent = "Эта покупка уже записана в домашние запасы.";
      card.appendChild(done);
      purchases.appendChild(card);
      continue;
    }

    const controls = document.createElement("div");
    controls.className = "shopping-status-controls";
    for (const [choiceStatus, label] of [
      [SHOPPING_STATUS.PICKED, "✓ Взял"],
      [SHOPPING_STATUS.NOT_FOUND, "Не нашёл"],
      [SHOPPING_STATUS.SKIPPED, "Не беру"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "shopping-status-button";
      button.classList.toggle("selected", entry.status === choiceStatus);
      button.textContent = label;
      button.disabled = Boolean(session?.completed) || state.shoppingConfirming;
      button.setAttribute("aria-pressed", entry.status === choiceStatus ? "true" : "false");
      button.addEventListener("click", () => setShoppingStatus(record, key, choiceStatus));
      controls.appendChild(button);
    }
    card.appendChild(controls);

    if (entry.status === SHOPPING_STATUS.PICKED) {
      const actual = document.createElement("div");
      actual.className = "shopping-actual";
      const question = document.createElement("div");
      question.className = "shopping-actual-copy";
      const strong = document.createElement("strong");
      strong.textContent = "Фактически взято";
      const note = document.createElement("small");
      note.textContent =
        entry.actualPacks === purchase.packs
          ? "Как в плане"
          : `Планировалось: ${packageText(purchase.packs)}`;
      question.append(strong, note);

      const stepperHolder = document.createElement("div");
      stepperHolder.appendChild(
        makeStepper(entry.actualPacks, (next) => setShoppingPacks(record, key, next), 1),
      );
      actual.append(question, stepperHolder);
      card.appendChild(actual);
    }

    purchases.appendChild(card);
  }

  renderShoppingActions(record, feasible);

  const explanation = byId("explanation-list");
  explanation.replaceChildren(...buildCoverageReason(record, context));

  if (preservedScroll !== null) {
    window.scrollTo(0, preservedScroll);
  } else {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function openStoredPlan(planId) {
  try {
    const record = await request(`/plans/${encodeURIComponent(planId)}`);
    renderPlan(record, null);
  } catch (error) {
    showToast(friendlyError(error), true);
  }
}

async function refreshOperationalState() {
  const [household, estimates, history, plans] = await Promise.all([
    request("/household/state"),
    request("/household/estimates"),
    request("/household/history"),
    request("/plans?limit=12"),
  ]);
  state.household = household.household;
  state.reports = estimates.reports || [];
  state.history = history.events || [];
  state.plans = plans.plans || [];
  renderFirstRun();
  renderHome();
  renderLearning();
  renderRecentPlans();
  renderActivity();
}

// M12.5 experimental stand for explicit routine preferences. Preview never
// creates a persisted plan, purchase or household inventory event.
function selectedRoutineSku(itemId, fallback) {
  const choices = skusForItem(itemId);
  const selected = state.usualBasketSkuChoices.get(itemId);
  if (selected) return choices.find((sku) => sku.sku_id === selected) || null;
  if (choices.length === 1) return choices[0];
  // A previously stored fallback is an exact quantity, not proof of which
  // retailer SKU the user intended. Do not silently choose among packages.
  return null;
}

function usualFallbackFromRatio(itemId, skuId, ratio) {
  const sku = skuById(skuId);
  if (!sku || sku.item_id !== itemId) {
    throw new Error("Сначала выберите конкретную упаковку.");
  }
  const parts = ratio === "half" ? [1, 2] : ratio === "one" ? [1, 1] : [2, 1];
  return {
    amount: scaleDecimalText(sku.package_quantity.amount, parts[0], parts[1]),
    unit: sku.package_quantity.unit,
  };
}

function usualFallbackRatio(itemId, fallback, sku) {
  if (!fallback) return "";
  if (!sku) return "custom";
  for (const ratio of ["half", "one", "two"]) {
    if (sameQuantity(fallback, usualFallbackFromRatio(itemId, sku.sku_id, ratio))) {
      return ratio;
    }
  }
  return "custom";
}

function invalidateUsualBasketPreview() {
  state.usualBasketPreviewRevision += 1;
  state.usualBasketPreviewId = null;
  const panel = byId("usual-basket-preview");
  panel.replaceChildren();
  panel.classList.add("hidden");
}

function markUsualBasketDirty() {
  state.usualBasketDirty = true;
  invalidateUsualBasketPreview();
  renderUsualBasket();
}

function renderUsualBasket() {
  const panel = byId("usual-basket-panel");
  panel.classList.toggle("hidden", !state.usualBasketAvailable);
  if (!state.usualBasketAvailable) return;
  const list = byId("usual-basket-list");
  list.replaceChildren();
  for (const item of state.catalog.items) {
    const skuChoices = skusForItem(item.item_id);
    if (!skuChoices.length) continue;
    const holder = document.createElement("div");
    holder.className = "usual-basket-item";
    const label = document.createElement("label");
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = state.usualBasketDraft.has(item.item_id);
    const title = document.createElement("span");
    title.textContent = `${itemEmoji(item.item_id)} ${item.name}`;
    label.append(enabled, title);
    const fallback = state.usualBasketDraft.get(item.item_id) || null;
    const reference = selectedRoutineSku(item.item_id, fallback);

    // Show retailer package identity before interpreting "one package" when
    // several canonical SKUs exist for the same household Item.
    const packageSelect = document.createElement("select");
    packageSelect.setAttribute("aria-label", `Упаковка для ${item.name}`);
    appendOption(packageSelect, "", "Выберите упаковку");
    for (const sku of skuChoices) {
      appendOption(
        packageSelect, sku.sku_id,
        `${sku.name} · ${humanQuantity(sku.package_quantity)}`,
      );
    }
    packageSelect.value = reference?.sku_id || "";
    packageSelect.disabled = !enabled.checked || state.usualBasketSaving;
    packageSelect.addEventListener("change", () => {
      const next = packageSelect.value;
      if (next) state.usualBasketSkuChoices.set(item.item_id, next);
      else state.usualBasketSkuChoices.delete(item.item_id);
      // Changing the reference package must not retain the old exact fallback.
      state.usualBasketDraft.set(item.item_id, null);
      markUsualBasketDirty();
    });

    const select = document.createElement("select");
    select.setAttribute("aria-label", `Резервный объём для ${item.name}`);
    for (const [value, caption] of [
      ["", "По истории / уточнить"],
      ["half", "½ упаковки"],
      ["one", "1 упаковка"],
      ["two", "2 упаковки"],
    ]) {
      appendOption(select, value, caption);
    }
    const ratio = usualFallbackRatio(item.item_id, fallback, reference);
    if (ratio === "custom") {
      appendOption(select, "custom", `Указано: ${humanQuantity(fallback)}`);
    }
    select.value = ratio;
    select.disabled = !enabled.checked || state.usualBasketSaving || !reference;
    enabled.disabled = state.usualBasketSaving;

    enabled.addEventListener("change", () => {
      if (enabled.checked) state.usualBasketDraft.set(item.item_id, null);
      else {
        state.usualBasketDraft.delete(item.item_id);
        state.usualBasketSkuChoices.delete(item.item_id);
      }
      markUsualBasketDirty();
    });
    select.addEventListener("change", () => {
      state.usualBasketDraft.set(
        item.item_id,
        select.value === "" ? null
          : select.value === "custom" ? fallback
          : usualFallbackFromRatio(item.item_id, reference.sku_id, select.value),
      );
      markUsualBasketDirty();
    });
    holder.append(label, packageSelect, select);
    list.appendChild(holder);
  }

  // An edited catalog can make saved preferences stale. Make each stale entry
  // removable rather than trapping the user into a full destructive reset.
  for (const itemId of state.usualBasketDraft.keys()) {
    if (itemById(itemId) && skusForItem(itemId).length) continue;
    const holder = document.createElement("div");
    holder.className = "usual-basket-item";
    const title = document.createElement("span");
    title.textContent = `Недоступен в каталоге: ${itemId}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button";
    remove.textContent = "Удалить";
    remove.disabled = state.usualBasketSaving;
    remove.addEventListener("click", () => {
      state.usualBasketDraft.delete(itemId);
      state.usualBasketSkuChoices.delete(itemId);
      markUsualBasketDirty();
    });
    holder.append(title, remove);
    list.appendChild(holder);
  }

  byId("usual-basket-status").textContent = state.usualBasketDirty
    ? "Есть несохранённые изменения. Сначала сохраните привычные товары."
    : `Сохранено привычных товаров: ${state.usualBasketDraft.size}`;
  byId("save-usual-basket").disabled = state.usualBasketSaving || !state.usualBasketDirty;
  byId("preview-usual-basket").disabled =
    state.usualBasketSaving || state.usualBasketDirty || state.usualBasketDraft.size === 0;
  const repeat = byId("repeat-usual-basket");
  repeat.classList.toggle("hidden", !state.usualBasketRepeatSettings);
  repeat.disabled =
    state.usualBasketSaving || state.usualBasketRepeating
    || state.usualBasketDirty || state.usualBasketDraft.size === 0;
  if (state.usualBasketRepeatSettings) {
    const settings = state.usualBasketRepeatSettings;
    repeat.textContent =
      `Повторить: ${settings.budget.amount} ${settings.budget.currency}, ${settings.horizon_days} дн.`;
  }
}

async function loadUsualBasket() {
  try {
    const data = await request("/household/usual-basket");
    state.usualBasketAvailable = true;
    state.usualBasketDraft = new Map(
      (data.usual_basket?.items || []).map((entry) => [entry.item_id, entry.fallback_quantity]),
    );
    state.usualBasketSkuChoices.clear();
    state.usualBasketDirty = false;
    invalidateUsualBasketPreview();
    await loadRepeatSettings();
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;
    state.usualBasketAvailable = false;
  }
  renderUsualBasket();
}


async function loadRepeatSettings() {
  const result = await request("/household/usual-basket/last-settings");
  state.usualBasketRepeatSettings = result.repeat_settings;
  renderUsualBasket();
}

function parseConfirmedHorizonDays(value) {
  // The domain accepts Decimal("7.0") and Decimal("1E+1"), while the stand
  // selects integral days. Normalize exactly, not via floating point rounding.
  const raw = String(value).trim();
  const parsed = /^\+?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?$/.exec(raw);
  if (!parsed || raw.length > 64) return null;
  const exponentText = raw.match(/[eE]([+-]?\d+)$/)?.[1] || "0";
  const exponent = Number(exponentText);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 30) return null;
  return parseHorizonDays(decimalText(raw));
}

function applyPreviousRoutineInputs(settings) {
  // These are solely user-approved planning inputs. Neither previous SKUs nor
  // quantities, prices, household state or market observations are replayed.
  const days = parseConfirmedHorizonDays(settings.horizon_days);
  const amount = normalizeNumberInput(String(settings.budget.amount));
  const currency = byId("plan-currency");
  // The current stand intentionally fixes currency to KGS in a hidden input.
  // Refuse a historic currency the stand cannot display or plan in.
  if (days === null || !amount || settings.budget.currency !== currency.value) {
    throw new Error("Прошлые настройки больше не поддерживаются этой формой.");
  }
  byId("plan-budget").value = amount;
  const preset = [...document.querySelectorAll("[data-days]")].find(
    (button) => Number(button.dataset.days) === days
  );
  if (preset) {
    setHorizonPreset(preset);
  } else {
    byId("custom-horizon-days").value = String(days);
    updateCustomHorizon();
    byId("custom-horizon-field").classList.remove("hidden");
    byId("custom-horizon-toggle").setAttribute("aria-expanded", "true");
  }
  invalidateUsualBasketPreview();
}

async function repeatUsualBasket() {
  if (state.usualBasketRepeating || state.usualBasketDirty || !state.usualBasketDraft.size) {
    return;
  }
  state.usualBasketRepeating = true;
  const revision = state.usualBasketPreviewRevision;
  renderUsualBasket();
  try {
    // Read the current last confirmed plan, not a stale browser-side copy.
    const current = await request("/household/usual-basket/last-settings");
    if (revision !== state.usualBasketPreviewRevision || state.usualBasketDirty) return;
    state.usualBasketRepeatSettings = current.repeat_settings;
    if (!current.repeat_settings) {
      showToast("Сначала подтвердите хотя бы одну привычную закупку.", true);
      return;
    }
    applyPreviousRoutineInputs(current.repeat_settings);
    await previewUsualBasket();
  } catch (error) {
    showToast(friendlyError(error), true);
  } finally {
    state.usualBasketRepeating = false;
    renderUsualBasket();
  }
}

async function saveUsualBasket() {
  if (state.usualBasketSaving || !state.usualBasketDirty) return;
  state.usualBasketSaving = true;
  renderUsualBasket();
  try {
    const items = [...state.usualBasketDraft].map(([item_id, fallback_quantity]) => ({
      item_id, fallback_quantity,
    }));
    const result = await request("/household/usual-basket", {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    state.usualBasketDraft = new Map(
      result.usual_basket.items.map((entry) => [entry.item_id, entry.fallback_quantity]),
    );
    state.usualBasketDirty = false;
    showToast("Привычные товары сохранены.");
  } catch (error) {
    showToast(friendlyError(error), true);
  } finally {
    state.usualBasketSaving = false;
    renderUsualBasket();
  }
}

async function previewUsualBasket() {
  if (state.usualBasketDirty || !state.usualBasketDraft.size) return;
  invalidateUsualBasketPreview();
  const revision = state.usualBasketPreviewRevision;
  const button = byId("preview-usual-basket");
  button.disabled = true;
  const panel = byId("usual-basket-preview");
  panel.classList.remove("hidden");
  try {
    const days = parseHorizonDays(byId("plan-horizon").value);
    if (days === null) throw new Error("Выберите положительный период.");
    const amount = normalizeNumberInput(byId("plan-budget").value);
    if (!amount) throw new Error("Укажите бюджет в форме ниже.");
    const response = await request("/household/usual-basket/preview", {
      method: "POST",
      body: JSON.stringify({
        budget: { amount, currency: byId("plan-currency").value },
        horizon_days: String(days),
      }),
    });
    // The form, routine, or a newer preview may have changed during fetch.
    // Never surface a token for inputs the user is no longer looking at.
    if (revision !== state.usualBasketPreviewRevision) return;
    const heading = document.createElement("strong");
    heading.textContent = response.ready
      ? "Предварительный результат"
      : "Нужно уточнить количество";
    panel.appendChild(heading);
    if (response.needs_clarification?.length) {
      const note = document.createElement("p");
      note.textContent = "Нет подтверждённой нормы или явно заданного количества для: "
        + response.needs_clarification.map(itemName).join(", ")
        + ". Выберите резервное количество и сохраните корзину.";
      panel.appendChild(note);
    }
    if (response.plan) {
      const note = document.createElement("p");
      note.textContent = response.plan.status === "feasible"
        ? `Ожидаемые расходы: ${moneyText(response.plan.total_cost)}`
        : "При текущем бюджете и рыночных данных план не найден.";
      panel.appendChild(note);
      if ((response.plan.purchases || []).length) {
        const list = document.createElement("ul");
        for (const purchase of response.plan.purchases) {
          const item = document.createElement("li");
          item.textContent = `${itemName(purchase.item_id)} · ${packageText(purchase.packs)} · ${moneyText(purchase.cost)}`;
          list.appendChild(item);
        }
        panel.appendChild(list);
      }
    }
    const evidence = document.createElement("p");
    evidence.textContent = (response.choices || []).map((choice) => {
      const source = choice.basis === "recurring" ? "по истории расхода"
        : choice.basis === "fallback" ? "по указанному количеству"
        : choice.basis === "override" ? "по разовому запросу" : "требует уточнения";
      return `${itemName(choice.item_id)} — ${source}`;
    }).join("; ");
    panel.appendChild(evidence);
    if (response.preview_id && response.plan?.status === "feasible") {
      state.usualBasketPreviewId = response.preview_id;
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.id = "confirm-usual-basket";
      confirmButton.className = "primary-button";
      confirmButton.textContent = "Сохранить этот список покупок";
      confirmButton.addEventListener("click", async () => {
        if (!state.usualBasketPreviewId || confirmButton.disabled) return;
        confirmButton.disabled = true;
        confirmButton.textContent = "Сохраняем…";
        try {
          const saved = await request("/household/usual-basket/confirm", {
            method: "POST",
            body: JSON.stringify({
              preview_id: state.usualBasketPreviewId,
            }),
          });
          state.usualBasketPreviewId = null;
          renderPlan(saved.plan);
          await refreshOperationalState();
          await loadRepeatSettings();
          panel.replaceChildren();
          const note = document.createElement("p");
          note.textContent = "Список сохранён. Покупки пока не совершены: отмечайте и подтверждайте их отдельно.";
          panel.appendChild(note);
          showToast("Список покупок сохранён.");
        } catch (error) {
          confirmButton.disabled = false;
          confirmButton.textContent = "Сохранить этот список покупок";
          if (error instanceof ApiError && error.status === 409) {
            state.usualBasketPreviewId = null;
            confirmButton.disabled = true;
            panel.textContent = "Данные изменились или предпросмотр устарел. Рассчитайте его снова.";
          } else {
            showToast(friendlyError(error), true);
          }
        }
      });
      panel.appendChild(confirmButton);
      const hint = document.createElement("p");
      hint.textContent = "Сохранение плана не записывает фактическую покупку или списание запасов.";
      panel.appendChild(hint);
    }
  } catch (error) {
    if (revision === state.usualBasketPreviewRevision) {
      state.usualBasketPreviewId = null;
      panel.textContent = friendlyError(error);
    }
  } finally {
    renderUsualBasket();
  }
}

// M12.8: recipe explanations are based only on recorded household facts.
// Quoting a recipe never writes a plan or a purchase event.
function recipeStatusText(recipe) {
  if (recipe.status === "covered") return "По учёту всё есть";
  if (recipe.status === "short") return `Докупить: ${recipe.missing_count} поз.`;
  return "Нужно уточнить остатки";
}

function renderRecipeList() {
  const list = byId("recipes-list");
  list.replaceChildren();
  const recipes = state.recipes;
  byId("recipes-status").textContent = recipes.length
    ? `Подобрано рецептов из каталога: ${recipes.length}. Это ориентир по учёту, не проверка продуктов в холодильнике.`
    : "В текущем каталоге нет рецептов с полным набором известных ингредиентов.";
  for (const recipe of recipes) {
    const card = document.createElement("details");
    card.className = "recipe-card";
    const summary = document.createElement("summary");
    summary.textContent = `${recipe.name} · ${recipeStatusText(recipe)}`;
    const meta = document.createElement("p");
    meta.className = "recipe-meta";
    meta.textContent = `${recipe.cuisine} · ${recipe.category} · ${recipe.servings} порции`;
    const ingredients = document.createElement("ul");
    for (const entry of recipe.ingredients) {
      const row = document.createElement("li");
      const status = entry.status === "covered" ? "есть по учёту"
        : entry.status === "short" ? `нужно ${humanQuantity(entry.missing)}`
        : "остаток неизвестен";
      row.textContent = `${itemName(entry.item_id)} — ${humanQuantity(entry.required)}; ${status}`;
      ingredients.appendChild(row);
    }
    const steps = document.createElement("ol");
    for (const step of recipe.steps) {
      const row = document.createElement("li");
      row.textContent = step;
      steps.appendChild(row);
    }
    const actions = document.createElement("div");
    actions.className = "recipe-actions";
    const quoteButton = document.createElement("button");
    quoteButton.type = "button";
    quoteButton.className = "secondary-button";
    quoteButton.textContent = "Проверить стоимость недостающего";
    const result = document.createElement("p");
    result.className = "recipe-quote";
    result.setAttribute("aria-live", "polite");
    quoteButton.addEventListener("click", async () => {
      const generation = state.recipeQuoteRevision;
      quoteButton.disabled = true;
      result.textContent = "Проверяем данные…";
      try {
        const budget = normalizeNumberInput(byId("plan-budget").value);
        if (!budget) throw new Error("Укажите бюджет в форме покупок.");
        const data = await request(`/recipes/${encodeURIComponent(recipe.recipe_id)}/quote`, {
          method: "POST",
          body: JSON.stringify({
            budget: {amount: budget, currency: byId("plan-currency").value},
          }),
        });
        if (generation !== state.recipeQuoteRevision) return;
        if (data.recipe.status === "unverified") {
          result.textContent = "Не все остатки проверены. Сначала уточните домашние запасы.";
        } else if (data.recipe.status === "covered") {
          result.textContent = "По учёту докупать ничего не нужно (0 сом).";
        } else if (data.plan?.status === "feasible") {
          const lines = (data.plan.purchases || []).map(
            (entry) => `${itemName(entry.item_id)}: ${packageText(entry.packs)}`,
          );
          result.textContent = `Стоимость доступных упаковок: ${moneyText(data.minimum_shop_cost)}. ${lines.join("; ")}. Это предпросмотр; покупки не оформлены.`;
        } else {
          result.textContent = "При текущем бюджете или предложениях магазинов не удалось покрыть все недостающие ингредиенты.";
        }
      } catch (error) {
        if (generation === state.recipeQuoteRevision) result.textContent = friendlyError(error);
      } finally {
        quoteButton.disabled = false;
      }
    });
    actions.append(quoteButton, result);
    card.append(summary, meta, ingredients, steps, actions);
    if (recipe.source_url) {
      const link = document.createElement("a");
      link.href = recipe.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `Первоисточник: ${recipe.attribution || "открыть"}`;
      card.appendChild(link);
    }
    list.appendChild(card);
  }
}

async function loadRecipes() {
  const response = await request("/recipes");
  state.recipes = response.recipes || [];
  state.recipeQuoteRevision += 1;
  renderRecipeList();
}

async function refreshAll() {
  try {
    setConnection(false, "Подключаемся…");
    const catalog = await request("/catalog");
    state.catalog = catalog.catalog;
    refillSkuSelect(byId("manual-purchase-sku"));
    renderProductPicker();
    renderMustHaves();
    await loadUsualBasket();
    await refreshOperationalState();
    await loadRecipes();
    setConnection(true, "Работает");
  } catch (error) {
    setConnection(false, "Нет связи");
    showToast(friendlyError(error), true);
  }
}

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => setView(button.dataset.view));
}

byId("start-home-setup").addEventListener("click", () => setView("home"));
byId("refresh-recipes").addEventListener("click", async () => {
  try {
    await loadRecipes();
  } catch (error) {
    showToast(friendlyError(error), true);
  }
});
byId("save-usual-basket").addEventListener("click", saveUsualBasket);
byId("preview-usual-basket").addEventListener("click", previewUsualBasket);
byId("repeat-usual-basket").addEventListener("click", repeatUsualBasket);
// Invalidate displayed plans when the user changes planning assumptions.
for (const input of ["plan-budget", "plan-currency", "plan-horizon", "custom-horizon-days"]) {
  byId(input).addEventListener("input", invalidateUsualBasketPreview);
  byId(input).addEventListener("change", invalidateUsualBasketPreview);
}
for (const chip of document.querySelectorAll("[data-days]")) {
  chip.addEventListener("click", invalidateUsualBasketPreview);
}
byId("custom-horizon-toggle").addEventListener("click", invalidateUsualBasketPreview);
byId("product-search").addEventListener("input", renderProductPicker);
byId("discard-pending-stocktakes").addEventListener("click", () => {
  if (state.savingStocktakes) return;
  state.pendingStocktakes.clear();
  renderHome();
});
byId("save-pending-stocktakes").addEventListener("click", savePendingStocktakes);
byId("home-filter-toggle").addEventListener("click", () => {
  if (state.savingStocktakes) return;
  state.showAllStockItems = !state.showAllStockItems;
  renderHome();
});
byId("finish-shopping").addEventListener("click", () => {
  if (state.activePlan) finishShoppingSession(state.activePlan);
});

function parseHorizonDays(value) {
  const raw = String(value).trim();
  if (!/^\d+$/.test(raw)) return null;
  const days = Number(raw);
  if (!Number.isSafeInteger(days) || days < 1) return null;
  return days;
}

function setHorizonPreset(button) {
  const days = parseHorizonDays(button.dataset.days);
  if (days === null) return;

  byId("plan-horizon").value = String(days);
  for (const choice of document.querySelectorAll("[data-days]")) {
    choice.classList.toggle("selected", choice === button);
  }

  const customField = byId("custom-horizon-field");
  const customToggle = byId("custom-horizon-toggle");
  const customInput = byId("custom-horizon-days");
  customField.classList.add("hidden");
  customField.classList.remove("selected");
  customToggle.classList.remove("selected");
  customToggle.setAttribute("aria-expanded", "false");
  customToggle.textContent = "Другой период";
  customInput.setCustomValidity("");
}

function toggleCustomHorizon() {
  const customField = byId("custom-horizon-field");
  const customToggle = byId("custom-horizon-toggle");
  const customInput = byId("custom-horizon-days");
  const opening = customField.classList.contains("hidden");

  if (!opening) {
    const selectedPreset = document.querySelector("[data-days].selected");
    if (selectedPreset) customInput.setCustomValidity("");
    customField.classList.add("hidden");
    customToggle.setAttribute("aria-expanded", "false");
    return;
  }

  customField.classList.remove("hidden");
  customToggle.setAttribute("aria-expanded", "true");

  const days = parseHorizonDays(customInput.value);
  if (days !== null) {
    byId("plan-horizon").value = String(days);
    for (const choice of document.querySelectorAll("[data-days]")) {
      choice.classList.remove("selected");
    }
    customField.classList.add("selected");
    customToggle.classList.add("selected");
    customToggle.textContent = `Другой период · ${dayText(days)}`;
  } else {
    customField.classList.remove("selected");
    customToggle.classList.remove("selected");
    customToggle.textContent = "Другой период";
  }

  window.setTimeout(() => customInput.focus(), 0);
}

function updateCustomHorizon() {
  const customInput = byId("custom-horizon-days");
  const customField = byId("custom-horizon-field");
  const customToggle = byId("custom-horizon-toggle");
  const days = parseHorizonDays(customInput.value);
  const hasValue = customInput.value.trim() !== "";
  const selectedPreset = document.querySelector("[data-days].selected");

  if (days === null) {
    customInput.setCustomValidity(
      hasValue ? "Введите целое количество дней от 1." : "",
    );
    if (!selectedPreset) byId("plan-horizon").value = "";
    customField.classList.remove("selected");
    customToggle.classList.remove("selected");
    customToggle.textContent = "Другой период";
    return;
  }

  customInput.setCustomValidity("");
  byId("plan-horizon").value = String(days);
  for (const choice of document.querySelectorAll("[data-days]")) {
    choice.classList.remove("selected");
  }
  customField.classList.add("selected");
  customToggle.classList.add("selected");
  customToggle.textContent = `Другой период · ${dayText(days)}`;
}

for (const button of document.querySelectorAll("[data-days]")) {
  button.addEventListener("click", () => setHorizonPreset(button));
}

byId("custom-horizon-toggle").addEventListener("click", toggleCustomHorizon);
byId("custom-horizon-days").addEventListener("input", updateCustomHorizon);

for (const button of document.querySelectorAll("[data-step-target]")) {
  button.addEventListener("click", () => {
    const input = byId(button.dataset.stepTarget);
    const current = Number(input.value) || 1;
    const step = Number(button.dataset.step);
    input.value = String(Math.max(1, current + step));
  });
}

byId("manual-purchase-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  try {
    button.disabled = true;
    const packs = Number(byId("manual-purchase-packs").value);
    if (!Number.isInteger(packs) || packs < 1) throw new Error("Количество упаковок должно быть целым числом.");
    const form = byId("manual-purchase-form");
    await request("/household/purchases", {
      method: "POST",
      body: JSON.stringify({
        event_id: formEventId(form, "purchase"),
        sku_id: byId("manual-purchase-sku").value,
        packs,
      }),
    });
    clearFormEventId(form);
    showToast("Покупка добавлена в домашние запасы.");
    await refreshOperationalState();
  } catch (error) {
    showToast(friendlyError(error), true);
  } finally {
    button.disabled = false;
  }
});

byId("plan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("build-plan-button");
  try {
    button.disabled = true;
    const horizonDays = parseHorizonDays(byId("plan-horizon").value);
    if (horizonDays === null) {
      const customField = byId("custom-horizon-field");
      const customToggle = byId("custom-horizon-toggle");
      const customInput = byId("custom-horizon-days");
      customField.classList.remove("hidden");
      customToggle.classList.add("selected");
      customToggle.setAttribute("aria-expanded", "true");
      customInput.setCustomValidity("Введите целое количество дней от 1.");
      customInput.reportValidity();
      throw new Error("Укажите период целым числом дней.");
    }

    const budget = normalizeNumberInput(byId("plan-budget").value);
    if (!budget) throw new Error("Укажите бюджет.");
    const payload = {
      budget: { amount: budget, currency: byId("plan-currency").value },
      horizon_days: String(horizonDays),
      explicit_needs: collectMustHaves(),
    };
    const response = await request("/plans", { method: "POST", body: JSON.stringify(payload) });
    renderPlan(response.plan, response.household || null);
    showToast("Покупки готовы.");
    await refreshOperationalState();
  } catch (error) {
    showToast(friendlyError(error), true);
  } finally {
    button.disabled = false;
  }
});

refreshAll();


// M12 first-use bootstrap. This layer only orchestrates existing M10 stocktake
// commands. Package-relative choices are explicit quantities; fuzzy labels such
// as "немного" or "много" never become authoritative household facts.
(() => {
  const FIRST_USE_DISMISSED_KEY = "hsp:first-use-dismissed";
  const COMMON_ITEM_ORDER = [
    "milk",
    "eggs",
    "bread",
    "pasta",
    "sunflower_oil",
    "rice",
    "sugar",
    "semolina",
    "canned_peas",
    "canned_fish",
    "seasoning",
    "oil",
  ];

  const onboarding = {
    step: "welcome",
    selected: new Set(),
    quantities: new Map(),
    saving: false,
    missingItemIds: [],
  };

  const layer = byId("onboarding-layer");
  const content = byId("onboarding-content");
  const actions = byId("onboarding-actions");
  const progress = byId("onboarding-progress");
  const skip = byId("onboarding-skip");
  const firstRunCard = byId("first-run-card");

  function button(label, className = "secondary-button") {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    return node;
  }

  function paragraph(text, className = "") {
    const node = document.createElement("p");
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function title(text) {
    const node = document.createElement("h2");
    node.id = "onboarding-title";
    node.textContent = text;
    return node;
  }

  function availableItems() {
    const rank = new Map(COMMON_ITEM_ORDER.map((itemId, index) => [itemId, index]));
    return [...state.catalog.items]
      .filter((item) => primarySku(item.item_id))
      .sort((left, right) => {
        const leftRank = rank.get(left.item_id) ?? 10_000;
        const rightRank = rank.get(right.item_id) ?? 10_000;
        if (leftRank !== rightRank) return leftRank - rightRank;
        return left.name.localeCompare(right.name, "ru");
      })
      .slice(0, 10);
  }

  function setProgress(label) {
    progress.textContent = label;
  }

  function clearSurface() {
    content.replaceChildren();
    actions.replaceChildren();
  }

  function openOnboarding() {
    if (sessionStorage.getItem(FIRST_USE_DISMISSED_KEY) === "1") return;
    if (!state.catalog.items.length || state.history.length > 0) return;
    layer.classList.remove("hidden");
    layer.setAttribute("aria-hidden", "false");
    document.body.classList.add("onboarding-open");
    renderOnboarding();
  }

  function closeOnboarding() {
    layer.classList.add("hidden");
    layer.setAttribute("aria-hidden", "true");
    document.body.classList.remove("onboarding-open");
  }

  function dismissOnboarding() {
    sessionStorage.setItem(FIRST_USE_DISMISSED_KEY, "1");
    closeOnboarding();
  }

  function renderWelcome() {
    setProgress("Шаг 1 из 3");
    const icon = document.createElement("div");
    icon.className = "onboarding-hero-icon";
    icon.textContent = "🏠";
    content.append(
      icon,
      title("Добро пожаловать"),
      paragraph("Отметьте несколько обычных продуктов и сколько их сейчас дома. Этого достаточно, чтобы начать."),
    );

    const note = document.createElement("div");
    note.className = "onboarding-note";
    note.textContent = "Не нужно описывать весь дом и не нужно считать идеально до грамма. Вы будете выбирать понятные доли упаковки.";
    content.appendChild(note);

    const start = button("Начать", "primary-button onboarding-primary");
    start.addEventListener("click", () => {
      onboarding.step = "products";
      renderOnboarding();
    });
    actions.appendChild(start);
  }

  function renderProducts() {
    setProgress("Шаг 2 из 3");
    content.append(
      title("Что у вас обычно бывает дома?"),
      paragraph("Выберите только знакомые вам продукты. Потом список можно менять в любой момент."),
    );

    const grid = document.createElement("div");
    grid.className = "onboarding-product-grid";
    for (const item of availableItems()) {
      const sku = primarySku(item.item_id);
      const option = button("", "onboarding-product");
      option.classList.toggle("selected", onboarding.selected.has(item.item_id));
      option.setAttribute("aria-pressed", onboarding.selected.has(item.item_id) ? "true" : "false");

      const emoji = document.createElement("span");
      emoji.className = "onboarding-product-emoji";
      emoji.textContent = itemEmoji(item.item_id);
      const copy = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = item.name;
      const pack = document.createElement("small");
      pack.textContent = `Упаковка: ${humanQuantity(sku.package_quantity)}`;
      copy.append(name, pack);
      const mark = document.createElement("span");
      mark.className = "onboarding-check";
      mark.textContent = onboarding.selected.has(item.item_id) ? "✓" : "+";
      option.append(emoji, copy, mark);
      option.addEventListener("click", () => {
        if (onboarding.selected.has(item.item_id)) {
          onboarding.selected.delete(item.item_id);
          onboarding.quantities.delete(item.item_id);
        } else {
          onboarding.selected.add(item.item_id);
        }
        renderOnboarding();
      });
      grid.appendChild(option);
    }
    content.appendChild(grid);

    const counter = paragraph(
      onboarding.selected.size
        ? `Выбрано: ${onboarding.selected.size}`
        : "Выберите хотя бы один продукт.",
      "onboarding-counter",
    );
    content.appendChild(counter);

    const back = button("Назад");
    back.addEventListener("click", () => {
      onboarding.step = "welcome";
      renderOnboarding();
    });
    const next = button("Дальше", "primary-button onboarding-primary");
    next.disabled = onboarding.selected.size === 0;
    next.addEventListener("click", () => {
      onboarding.step = "stock";
      renderOnboarding();
    });
    actions.append(back, next);
  }

  function stockPresets() {
    return [
      ["Нет", 0, 1],
      ["Половина", 1, 2],
      ["1 упаковка", 1, 1],
      ["2 упаковки", 2, 1],
    ];
  }

  function renderStock() {
    setProgress("Шаг 3 из 3");
    content.append(
      title("Сколько сейчас есть?"),
      paragraph("Выберите ближайший вариант для каждого продукта. Мы сохраним именно выбранную долю упаковки."),
    );

    const list = document.createElement("div");
    list.className = "onboarding-stock-list";
    for (const itemId of onboarding.selected) {
      const sku = primarySku(itemId);
      if (!sku) continue;
      const row = document.createElement("article");
      row.className = "onboarding-stock-row";

      const heading = document.createElement("div");
      heading.className = "onboarding-stock-heading";
      const name = document.createElement("strong");
      name.textContent = `${itemEmoji(itemId)} ${itemName(itemId)}`;
      const pack = document.createElement("small");
      pack.textContent = `Одна упаковка: ${humanQuantity(sku.package_quantity)}`;
      heading.append(name, pack);

      const choices = document.createElement("div");
      choices.className = "onboarding-stock-choices";
      const selected = onboarding.quantities.get(itemId);
      for (const [label, numerator, denominator] of stockPresets()) {
        const choice = button(label, "onboarding-stock-choice");
        const active = selected?.numerator === numerator && selected?.denominator === denominator;
        choice.classList.toggle("selected", active);
        choice.setAttribute("aria-pressed", active ? "true" : "false");
        choice.addEventListener("click", () => {
          onboarding.quantities.set(itemId, { numerator, denominator, label });
          renderOnboarding();
        });
        choices.appendChild(choice);
      }
      row.append(heading, choices);
      list.appendChild(row);
    }
    content.appendChild(list);

    const unanswered = [...onboarding.selected].filter((itemId) => !onboarding.quantities.has(itemId));
    if (unanswered.length) {
      content.appendChild(paragraph(`Осталось отметить: ${unanswered.length}`, "onboarding-counter"));
    }

    const back = button("Назад");
    back.disabled = onboarding.saving;
    back.addEventListener("click", () => {
      onboarding.step = "products";
      renderOnboarding();
    });
    const save = button(onboarding.saving ? "Сохраняем…" : "Сохранить запасы", "primary-button onboarding-primary");
    save.disabled = onboarding.saving || unanswered.length > 0;
    save.addEventListener("click", saveBootstrapStocktakes);
    actions.append(back, save);
  }

  async function saveBootstrapStocktakes() {
    if (onboarding.saving) return;
    onboarding.saving = true;
    renderOnboarding();
    const operationKeys = [];
    try {
      const missing = [];
      for (const itemId of onboarding.selected) {
        const sku = primarySku(itemId);
        const selected = onboarding.quantities.get(itemId);
        if (!sku || !selected) throw new Error("Не удалось подготовить выбранный остаток.");
        const amount = scaleDecimalText(
          sku.package_quantity.amount,
          selected.numerator,
          selected.denominator,
        );
        const operationKey = `hsp:first-use:${itemId}:${amount}:${sku.package_quantity.unit}`;
        let operationId = sessionStorage.getItem(operationKey);
        if (!operationId) {
          operationId = eventId("bootstrap-stocktake");
          sessionStorage.setItem(operationKey, operationId);
        }
        operationKeys.push(operationKey);
        await request("/household/stocktakes", {
          method: "POST",
          body: JSON.stringify({
            event_id: operationId,
            item_id: itemId,
            quantity: { amount, unit: sku.package_quantity.unit },
            reason: "first-use bootstrap",
          }),
        });
        if (selected.numerator === 0) missing.push(itemId);
      }

      for (const operationKey of operationKeys) sessionStorage.removeItem(operationKey);
      onboarding.missingItemIds = missing;
      for (const itemId of missing) {
        if (!state.mustHaves.has(itemId)) state.mustHaves.set(itemId, 1);
      }
      renderMustHaves();
      await refreshOperationalState();
      onboarding.step = "done";
      showToast("Начальные запасы сохранены.");
    } catch (error) {
      showToast(friendlyError(error), true);
    } finally {
      onboarding.saving = false;
      renderOnboarding();
    }
  }

  function renderDone() {
    setProgress("Готово");
    const icon = document.createElement("div");
    icon.className = "onboarding-hero-icon done";
    icon.textContent = "✓";
    content.append(icon, title("Можно начинать"));

    if (onboarding.missingItemIds.length) {
      content.appendChild(paragraph(
        "То, что обычно бывает дома, но сейчас закончилось, уже добавлено в обязательные покупки. Перед расчётом вы сможете всё изменить.",
      ));
      const missing = document.createElement("div");
      missing.className = "onboarding-missing-list";
      for (const itemId of onboarding.missingItemIds) {
        const chip = document.createElement("span");
        chip.textContent = `${itemEmoji(itemId)} ${itemName(itemId)}`;
        missing.appendChild(chip);
      }
      content.appendChild(missing);
    } else {
      content.appendChild(paragraph(
        "Запасы сохранены. Если нужно купить что-то прямо сейчас, добавьте продукт в «Нужно что-то обязательно?» перед расчётом.",
      ));
    }

    const home = button("Посмотреть запасы");
    home.addEventListener("click", () => {
      closeOnboarding();
      setView("home");
    });
    const shopping = button("Составить покупки", "primary-button onboarding-primary");
    shopping.addEventListener("click", () => {
      closeOnboarding();
      setView("shopping");
      byId("plan-budget")?.focus();
    });
    actions.append(home, shopping);
  }

  function renderOnboarding() {
    clearSurface();
    skip.classList.toggle("hidden", onboarding.step === "done");
    if (onboarding.step === "welcome") renderWelcome();
    else if (onboarding.step === "products") renderProducts();
    else if (onboarding.step === "stock") renderStock();
    else renderDone();
  }

  skip.addEventListener("click", dismissOnboarding);
  layer.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && onboarding.step !== "done") dismissOnboarding();
  });

  const firstRunObserver = new MutationObserver(() => {
    if (!firstRunCard.classList.contains("hidden")) openOnboarding();
  });
  firstRunObserver.observe(firstRunCard, { attributes: true, attributeFilter: ["class"] });
  if (!firstRunCard.classList.contains("hidden")) openOnboarding();
})();
