from __future__ import annotations

from .recipe_api import RecipeWebApi

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from household_supply.application import (
    JsonApiHandler,
    JsonApiResponse,
    LocalDataResetError,
    LocalDataResetService,
)
from household_supply.domain import CatalogSnapshot

from .usual_basket_api import UsualBasketWebApi


_MISSING_OFFER_PREFIX = "no available compatible offer can cover required item: "


def _quantity(value) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def serialize_web_catalog(catalog: CatalogSnapshot) -> dict[str, Any]:
    """Serialize only canonical catalog data needed by a local human UI.

    Retailer listing identities and market observations intentionally remain on the
    planning/market evidence side. The browser needs canonical items and purchasable
    package options, not authority to resolve retailer identity itself.
    """

    items_by_id = {}
    for sku in catalog.skus:
        items_by_id.setdefault(sku.item.id, sku.item)

    return {
        "items": [
            {
                "item_id": item.id,
                "name": item.canonical_name,
                "category": item.category,
                "aliases": list(item.aliases),
            }
            for item in sorted(items_by_id.values(), key=lambda item: item.id)
        ],
        "skus": [
            {
                "sku_id": sku.id,
                "item_id": sku.item.id,
                "name": sku.name,
                "brand": sku.brand,
                "package_quantity": _quantity(sku.package_quantity),
            }
            for sku in sorted(catalog.skus, key=lambda sku: sku.id)
        ],
    }


def _item_names(catalog: CatalogSnapshot) -> dict[str, str]:
    names: dict[str, str] = {}
    for sku in catalog.skus:
        names.setdefault(sku.item.id, sku.item.canonical_name)
    return names


def _humanize_infeasible_result(
    result: Mapping[str, Any], catalog: CatalogSnapshot
) -> dict[str, Any]:
    """Translate planner diagnostics only at the browser presentation boundary."""

    browser_result = dict(result)
    if browser_result.get("status") == "feasible":
        return browser_result

    if browser_result.get("minimum_required_cost") is not None:
        return browser_result

    raw_reasons = browser_result.get("infeasibility_reasons")
    reasons = list(raw_reasons) if isinstance(raw_reasons, (list, tuple)) else []
    missing_item_ids: list[str] = []
    for reason in reasons:
        if not isinstance(reason, str) or not reason.startswith(_MISSING_OFFER_PREFIX):
            continue
        item_id = reason[len(_MISSING_OFFER_PREFIX) :].strip()
        if item_id:
            missing_item_ids.append(item_id)

    if missing_item_ids:
        names = _item_names(catalog)
        labels = [names.get(item_id, item_id) for item_id in missing_item_ids]
        browser_result["infeasibility_reasons"] = [
            "Сейчас не удалось найти подходящее предложение для: "
            + ", ".join(labels)
            + ". Попробуйте обновить цены, изменить количество или убрать этот продукт "
            "из обязательных покупок."
        ]
    elif reasons:
        browser_result["infeasibility_reasons"] = [
            "Текущие ограничения не позволяют составить полный список покупок. "
            "Попробуйте изменить обязательные продукты или количество и рассчитать снова."
        ]

    return browser_result


def _humanize_plan_record(
    record: Mapping[str, Any], catalog: CatalogSnapshot
) -> dict[str, Any]:
    browser_record = dict(record)
    result = browser_record.get("result")
    if isinstance(result, Mapping):
        browser_record["result"] = _humanize_infeasible_result(result, catalog)
    return browser_record


def _humanize_browser_response(
    response: JsonApiResponse, catalog: CatalogSnapshot
) -> JsonApiResponse:
    body = dict(response.body)
    plan = body.get("plan")
    if isinstance(plan, Mapping):
        body["plan"] = _humanize_plan_record(plan, catalog)
    elif isinstance(body.get("result"), Mapping):
        body = _humanize_plan_record(body, catalog)
    return JsonApiResponse(response.status, body)


@dataclass(frozen=True, slots=True)
class HouseholdWebJsonApi:
    """Read-only browser bootstrap surface over an existing application API.

    All household mutations and planning still go through the wrapped M10 API.
    M11 adds only canonical catalog discovery required to render a client without
    duplicating catalog data inside JavaScript.
    """

    api: JsonApiHandler
    catalog: CatalogSnapshot
    reset_service: LocalDataResetService | None = None
    usual_basket_api: UsualBasketWebApi | None = None
    recipe_api: RecipeWebApi | None = None

    def accepts_json_body(self, method: str, path: str) -> bool:
        target = urlsplit(path)
        if (
            self.recipe_api is not None
            and self.recipe_api.accepts_json_body(method, path)
        ):
            return True
        if (
            self.usual_basket_api is not None
            and self.usual_basket_api.accepts_json_body(method, path)
        ):
            return True
        if (
            self.reset_service is not None
            and method.strip().upper() == "POST"
            and target.path == "/local-data/reset"
        ):
            return True
        policy = getattr(self.api, "accepts_json_body", None)
        return bool(callable(policy) and policy(method, path))

    def handle(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> JsonApiResponse:
        normalized_method = method.strip().upper()
        target = urlsplit(path)
        if target.scheme or target.netloc or target.fragment:
            return JsonApiResponse(400, {"error": "invalid_request_target"})

        if target.path == "/recipes" or target.path.startswith("/recipes/"):
            if self.recipe_api is None:
                return JsonApiResponse(404, {"error": "not_found"})
            return self.recipe_api.handle(method, path, payload)

        if target.path in {"/household/usual-basket", "/household/usual-basket/preview", "/household/usual-basket/confirm", "/household/usual-basket/last-settings"}:
            if self.usual_basket_api is None:
                return JsonApiResponse(404, {"error": "not_found"})
            return self.usual_basket_api.handle(method, path, payload)

        if target.path == "/catalog":
            if target.query:
                return JsonApiResponse(400, {"error": "invalid_query"})
            if normalized_method != "GET":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            return JsonApiResponse(200, {"catalog": serialize_web_catalog(self.catalog)})

        if target.path == "/local-data/reset":
            if target.query:
                return JsonApiResponse(400, {"error": "invalid_query"})
            if self.reset_service is None:
                return JsonApiResponse(404, {"error": "not_found"})
            if normalized_method != "POST":
                return JsonApiResponse(405, {"error": "method_not_allowed"})
            if payload is None:
                return JsonApiResponse(
                    400,
                    {"error": "invalid_request", "detail": "missing JSON body"},
                )
            if set(payload) != {"confirmation"}:
                return JsonApiResponse(
                    422,
                    {
                        "error": "invalid_request",
                        "detail": "reset request must contain only confirmation",
                    },
                )
            confirmation = payload.get("confirmation")
            if not isinstance(confirmation, str) or confirmation != "RESET":
                return JsonApiResponse(
                    422,
                    {
                        "error": "invalid_request",
                        "detail": "reset confirmation must equal RESET",
                    },
                )
            try:
                result = self.reset_service.reset()
            except LocalDataResetError as exc:
                return JsonApiResponse(
                    500, {"error": "reset_failed", "detail": str(exc)}
                )
            if self.usual_basket_api is not None and self.usual_basket_api.confirmation is not None:
                self.usual_basket_api.confirmation.discard_previews()
            return JsonApiResponse(
                200,
                {
                    "reset": {
                        "household_events_deleted": result.household_events_deleted,
                        "plans_deleted": result.plans_deleted,
                    }
                },
            )

        return _humanize_browser_response(
            self.api.handle(method, path, payload), self.catalog
        )
