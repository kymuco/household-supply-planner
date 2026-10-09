from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from household_supply.application import (
    ApplicationMarketError,
    ApplicationPlanRequest,
    ApplicationPlanResult,
    ApplicationRequestError,
    InventoryInput,
    PlanApplicationService,
    RequestedItem,
    UnknownCatalogItemError,
)
from household_supply.domain import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    MarketAcquisitionBatch,
    MarketObservation,
    Money,
    MultiObjectivePolicy,
    Quantity,
    SKU,
    SurplusPenaltyRate,
    Item,
)
from household_supply.market import MarketCompilationPolicy, StaticMarketProvider
from household_supply.application.lifecycle import serialize_market_evidence


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def make_service(
    *,
    milk_price: str = "120",
    oil_price: str = "190",
    price_currency: str = "KGS",
    clock=lambda: NOW,
    market_policy: MarketCompilationPolicy = MarketCompilationPolicy(),
) -> PlanApplicationService:
    milk = Item("milk", "Milk")
    oil = Item("oil", "Sunflower oil")
    milk_sku = SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l"))
    oil_sku = SKU("oil-1l", oil, "Oil 1L", Quantity(1, "l"))

    milk_key = ExternalListingKey("fixture", "store-a", "milk-1l")
    oil_key = ExternalListingKey("fixture", "store-a", "oil-1l")
    catalog = CatalogSnapshot(
        (milk_sku, oil_sku),
        (
            CatalogBinding(milk_key, milk_sku.id, "fixture"),
            CatalogBinding(oil_key, oil_sku.id, "fixture"),
        ),
    )
    batch = MarketAcquisitionBatch(
        provider_id="fixture",
        acquired_at=NOW,
        observations=(
            MarketObservation(
                id="obs-milk",
                provider_id="fixture",
                seller_id="store-a",
                external_product_id="milk-1l",
                price=Money(milk_price, price_currency),
                observed_at=NOW,
                package_quantity=Quantity(1, "l"),
                source_ref="fixture://milk",
            ),
            MarketObservation(
                id="obs-oil",
                provider_id="fixture",
                seller_id="store-a",
                external_product_id="oil-1l",
                price=Money(oil_price, price_currency),
                observed_at=NOW,
                package_quantity=Quantity(1, "l"),
                source_ref="fixture://oil",
            ),
        ),
    )
    return PlanApplicationService(
        catalog,
        (StaticMarketProvider(batch),),
        market_policy=market_policy,
        clock=clock,
    )


def make_request() -> ApplicationPlanRequest:
    return ApplicationPlanRequest(
        demands=(
            RequestedItem("milk", Quantity(1500, "ml")),
            RequestedItem("oil", Quantity(500, "ml")),
        ),
        inventory=(InventoryInput("milk-open", "milk", Quantity(500, "ml")),),
        budget=Money(1000, "KGS"),
    )


def test_application_service_runs_market_to_planner_vertical_slice() -> None:
    result = make_service().plan(make_request())

    assert result.plan.status.value == "feasible"
    assert result.plan.total_cost == Money(310, "KGS")
    assert result.plan.budget_remaining == Money(690, "KGS")
    assert [(p.offer.sku.id, p.packs) for p in result.plan.purchases] == [
        ("milk-1l", 1),
        ("oil-1l", 1),
    ]
    assert result.market_compilation.snapshot.captured_at == NOW
    assert len(result.market_compilation.snapshot.offers) == 2

def test_application_service_keeps_original_provider_prices_and_provenance() -> None:
    service = make_service()
    result = service.plan(make_request())

    assert result.market_compilation.batches == (service.providers[0].batch,)
    prices = {
        offer.sku.id: offer.price
        for offer in result.market_compilation.snapshot.offers
    }
    assert prices == {
        "milk-1l": Money("120", "KGS"),
        "oil-1l": Money("190", "KGS"),
    }

    evidence = serialize_market_evidence(result.market_compilation)
    observed = {
        entry["id"]: entry
        for entry in evidence["batches"][0]["observations"]
    }
    assert observed["obs-milk"]["price"] == {
        "amount": "120", "currency": "KGS"
    }
    assert observed["obs-milk"]["source_ref"] == "fixture://milk"


def test_application_service_rejects_unquoted_foreign_market_prices() -> None:
    request = ApplicationPlanRequest(
        demands=(RequestedItem("milk", Quantity(1, "l")),),
        budget=Money("2000", "KZT"),
    )
    with pytest.raises(ApplicationMarketError, match="explicit FX conversion"):
        make_service().plan(request)


def test_application_service_accepts_native_market_currency_without_fx() -> None:
    request = ApplicationPlanRequest(
        demands=(RequestedItem("milk", Quantity(1, "l")),),
        budget=Money("2000", "KZT"),
    )
    service = make_service(price_currency="KZT")
    result = service.plan(request)

    assert result.plan.status.value == "feasible"
    assert result.plan.total_cost == Money("120", "KZT")
    assert result.market_compilation.batches == (service.providers[0].batch,)


def test_application_request_defaults_to_exact_zero_objective_policy() -> None:
    result = make_service().plan(make_request())
    assert result.objective_policy == MultiObjectivePolicy.zero("KGS")
    assert result.plan.objective_breakdown is not None
    assert result.plan.objective_breakdown.total_score == result.plan.total_cost


def test_application_service_preserves_explicit_objective_policy() -> None:
    request = ApplicationPlanRequest(
        demands=(RequestedItem("milk", Quantity(1, "l")),),
        budget=Money(1000, "KGS"),
        objective_policy=MultiObjectivePolicy(
            Money(75, "KGS"),
            (SurplusPenaltyRate("milk", Money("0.01", "KGS")),),
        ),
    )
    result = make_service().plan(request)
    assert result.objective_policy == request.objective_policy


def test_application_service_rejects_unknown_demand_item() -> None:
    request = ApplicationPlanRequest(
        demands=(RequestedItem("unknown", Quantity(1, "piece")),),
        budget=Money(1000, "KGS"),
    )
    with pytest.raises(UnknownCatalogItemError, match="unknown"):
        make_service().plan(request)


def test_application_service_rejects_unknown_inventory_item() -> None:
    request = ApplicationPlanRequest(
        demands=(RequestedItem("milk", Quantity(1, "l")),),
        inventory=(InventoryInput("x", "unknown", Quantity(1, "piece")),),
        budget=Money(1000, "KGS"),
    )
    with pytest.raises(UnknownCatalogItemError, match="unknown"):
        make_service().plan(request)


def test_application_request_rejects_duplicate_demands() -> None:
    with pytest.raises(ApplicationRequestError, match="duplicate demand"):
        ApplicationPlanRequest(
            demands=(
                RequestedItem("milk", Quantity(1, "l")),
                RequestedItem("milk", Quantity(500, "ml")),
            ),
            budget=Money(1000, "KGS"),
        )


def test_application_request_rejects_duplicate_inventory_lot_ids() -> None:
    with pytest.raises(ApplicationRequestError, match="duplicate inventory"):
        ApplicationPlanRequest(
            demands=(RequestedItem("milk", Quantity(1, "l")),),
            inventory=(
                InventoryInput("same", "milk", Quantity(100, "ml")),
                InventoryInput("same", "oil", Quantity(100, "ml")),
            ),
            budget=Money(1000, "KGS"),
        )


def test_application_request_rejects_objective_currency_mismatch() -> None:
    with pytest.raises(ApplicationRequestError, match="currency"):
        ApplicationPlanRequest(
            demands=(RequestedItem("milk", Quantity(1, "l")),),
            budget=Money(1000, "KGS"),
            objective_policy=MultiObjectivePolicy.zero("USD"),
        )


def test_application_result_is_self_validating() -> None:
    result = make_service().plan(make_request())
    with pytest.raises(ValueError, match="planning problem"):
        ApplicationPlanResult(
            request=result.request,
            market_compilation=result.market_compilation,
            problem=make_service(milk_price="999").plan(make_request()).problem,
            objective_policy=result.objective_policy,
            plan=result.plan,
        )


def test_application_inputs_are_immutable_snapshots() -> None:
    demands = [RequestedItem("milk", Quantity(1, "l"))]
    inventory = [InventoryInput("lot", "milk", Quantity(100, "ml"))]
    request = ApplicationPlanRequest(demands, Money(1000, "KGS"), inventory)
    demands.append(RequestedItem("oil", Quantity(1, "l")))
    inventory.clear()
    assert [d.item_id for d in request.demands] == ["milk"]
    assert [lot.lot_id for lot in request.inventory] == ["lot"]


def test_application_numeric_values_remain_exact() -> None:
    result = make_service(milk_price="121.49", oil_price="193").plan(
        ApplicationPlanRequest(
            demands=(
                RequestedItem("milk", Quantity(1500, "ml")),
                RequestedItem("oil", Quantity(500, "ml")),
            ),
            budget=Money(5000, "KGS"),
        )
    )
    assert result.plan.total_cost.amount == Decimal("435.98")


def test_application_clock_drives_market_freshness() -> None:
    service = make_service(
        clock=lambda: NOW + timedelta(hours=2),
        market_policy=MarketCompilationPolicy(max_observation_age=timedelta(hours=1)),
    )
    result = service.plan(make_request())
    assert result.plan.status.value == "infeasible"
    assert not result.market_compilation.snapshot.offers
    assert {d.status.value for d in result.market_compilation.dispositions} == {"stale"}


def test_application_rejects_clock_before_acquisition() -> None:
    service = make_service(clock=lambda: NOW - timedelta(seconds=1))
    with pytest.raises(RuntimeError, match="precedes"):
        service.plan(make_request())


def test_application_rejects_naive_capture_clock() -> None:
    service = make_service(clock=lambda: NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezone-aware"):
        service.plan(make_request())


def test_application_rejects_dead_surplus_penalty() -> None:
    with pytest.raises(ApplicationRequestError, match="not present in request demands"):
        ApplicationPlanRequest(
            demands=(RequestedItem("milk", Quantity(1, "l")),),
            budget=Money(1000, "KGS"),
            objective_policy=MultiObjectivePolicy(
                Money.zero("KGS"),
                (SurplusPenaltyRate("oil", Money("0.01", "KGS")),),
            ),
        )


def test_application_rejects_incompatible_demand_unit_before_acquisition() -> None:
    service = make_service()
    request = ApplicationPlanRequest(
        demands=(RequestedItem("milk", Quantity(1, "piece")),),
        budget=Money(1000, "KGS"),
    )
    with pytest.raises(ApplicationRequestError, match="incompatible"):
        service.plan(request)


def test_application_service_requires_unique_provider_ids() -> None:
    service = make_service()
    provider = service.providers[0]
    with pytest.raises(ValueError, match="duplicate provider_id"):
        PlanApplicationService(service.catalog, (provider, provider), clock=lambda: NOW)


def test_application_service_requires_at_least_one_provider() -> None:
    service = make_service()
    with pytest.raises(ValueError, match="at least one"):
        PlanApplicationService(service.catalog, (), clock=lambda: NOW)


def test_invalid_request_is_rejected_before_market_acquisition() -> None:
    service = make_service()

    class CountingProvider:
        provider_id = "fixture"

        def __init__(self, batch):
            self.batch = batch
            self.calls = 0

        def acquire(self):
            self.calls += 1
            return self.batch

    provider = CountingProvider(service.providers[0].batch)
    guarded = PlanApplicationService(service.catalog, (provider,), clock=lambda: NOW)
    request = ApplicationPlanRequest(
        demands=(RequestedItem("not-in-catalog", Quantity(1, "piece")),),
        budget=Money(1000, "KGS"),
    )
    with pytest.raises(UnknownCatalogItemError):
        guarded.plan(request)
    assert provider.calls == 0


def test_market_provider_failure_is_normalized_at_application_boundary() -> None:
    from household_supply.application import ApplicationMarketError

    service = make_service()

    class FailingProvider:
        provider_id = "fixture"

        def acquire(self):
            raise RuntimeError("network exploded")

    failing = PlanApplicationService(service.catalog, (FailingProvider(),), clock=lambda: NOW)
    with pytest.raises(ApplicationMarketError, match="fixture") as raised:
        failing.plan(make_request())
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_application_rejects_inventory_dimension_mismatch_with_active_demand() -> None:
    service = make_service()
    request = ApplicationPlanRequest(
        demands=(RequestedItem("milk", Quantity(1, "l")),),
        inventory=(InventoryInput("lot", "milk", Quantity(1, "piece")),),
        budget=Money(1000, "KGS"),
    )
    with pytest.raises(ApplicationRequestError, match="inventory quantity unit"):
        service.plan(request)
