"""M11 local web MVP assembly.

Run a durable local demo:

    python -m pip install -e ".[dev,web]"
    python examples/m11_local_web.py --serve

Then open http://127.0.0.1:8765/ in a browser.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from household_supply.application import (
    FilePlanRepository,
    HouseholdClosedLoopJsonApi,
    DemandScopedPlanApplicationService,
    PlanApplicationService,
    PlanLifecycleService,
    HouseholdReplenishmentService,
    LocalDataResetService,
    FileUsualBasketRepository,
    UsualBasketPreparationService,
    UsualBasketPlanCommitService,
)
from household_supply.domain import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    Item,
    MarketAcquisitionBatch,
    MarketObservation,
    Money,
    Quantity,
    SKU,
)
from household_supply.household import FileHouseholdEventRepository, HouseholdLearningService
from household_supply.market import GlobusCatalogProviderFactory
from household_supply.web import (
    HouseholdLocalWebApp,
    HouseholdWebJsonApi,
    serve_local_web,
    UsualBasketWebApi,
    RecipeWebApi,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DemoMarketEntry:
    sku: SKU
    price: Money


class CurrentDemoMarketProvider:
    provider_id = "m11-demo"

    def __init__(self, entries: tuple[DemoMarketEntry, ...]) -> None:
        self.entries = entries

    def acquire(self) -> MarketAcquisitionBatch:
        now = utc_now()
        return MarketAcquisitionBatch(
            provider_id=self.provider_id,
            acquired_at=now,
            observations=tuple(
                MarketObservation(
                    id=f"{entry.sku.id}-{now.strftime('%Y%m%d%H%M%S%f')}",
                    provider_id=self.provider_id,
                    seller_id="demo-store",
                    external_product_id=entry.sku.id,
                    price=entry.price,
                    observed_at=now,
                    package_quantity=entry.sku.package_quantity,
                    name=entry.sku.name,
                    brand=entry.sku.brand,
                    source_ref=f"demo://{entry.sku.id}",
                )
                for entry in self.entries
            ),
        )


def build_demo_catalog() -> tuple[CatalogSnapshot, tuple[DemoMarketEntry, ...]]:
    milk = Item("milk", "Молоко", "dairy")
    rice = Item("rice", "Рис", "pantry")
    oil = Item("oil", "Масло растительное", "pantry")
    entries = (
        DemoMarketEntry(SKU("milk-1l", milk, "Молоко 1 л", Quantity("1", "l")), Money("120", "KGS")),
        DemoMarketEntry(SKU("rice-1kg", rice, "Рис 1 кг", Quantity("1", "kg")), Money("95", "KGS")),
        DemoMarketEntry(SKU("oil-1l", oil, "Масло растительное 1 л", Quantity("1", "l")), Money("190", "KGS")),
    )
    bindings = tuple(
        CatalogBinding(
            ExternalListingKey("m11-demo", "demo-store", entry.sku.id),
            entry.sku.id,
            "m11 offline demo",
        )
        for entry in entries
    )
    return CatalogSnapshot(tuple(entry.sku for entry in entries), bindings), entries


def _build_web_app(
    data_dir: Path,
    *,
    catalog: CatalogSnapshot,
    planner,
    allow_remote_hosts: bool = False,
) -> HouseholdLocalWebApp:
    household_repository = FileHouseholdEventRepository(
        data_dir / "household-events"
    )
    plan_repository = FilePlanRepository(data_dir / "plans")
    household = HouseholdLearningService(household_repository)
    lifecycle = PlanLifecycleService(
        planner,
        plan_repository,
        clock=utc_now,
    )
    replenishment = HouseholdReplenishmentService(
        household,
        lifecycle,
        clock=utc_now,
    )
    closed_loop = HouseholdClosedLoopJsonApi(replenishment)
    usual_basket_repo = FileUsualBasketRepository(data_dir / "usual-basket.json")
    usual_basket = UsualBasketPreparationService(
        household, catalog, usual_basket_repo, clock=utc_now,
    )
    reset_service = LocalDataResetService(
        household, plan_repository, usual_basket_repository=usual_basket_repo,
    )
    web_api = HouseholdWebJsonApi(
        closed_loop, catalog, reset_service,
        recipe_api=RecipeWebApi(household, planner, clock=utc_now),
        usual_basket_api=UsualBasketWebApi(
            usual_basket, planner,
            confirmation=UsualBasketPlanCommitService(usual_basket, lifecycle),
        ),
    )
    return HouseholdLocalWebApp(web_api, allow_non_loopback_hosts=allow_remote_hosts)


def build_demo_app(data_dir: Path, *, allow_remote_hosts: bool = False) -> HouseholdLocalWebApp:
    catalog, entries = build_demo_catalog()
    planner = PlanApplicationService(
        catalog,
        (CurrentDemoMarketProvider(entries),),
        clock=utc_now,
    )
    return _build_web_app(
        data_dir,
        catalog=catalog,
        planner=planner,
        allow_remote_hosts=allow_remote_hosts,
    )


def build_live_globus_app(
    data_dir: Path, *, allow_remote_hosts: bool = False
) -> HouseholdLocalWebApp:
    # Lazy import keeps the default offline M11 example deterministic while the
    # live mode consumes the separately maintained M5.1 catalog pack.
    from household_supply.market.catalogs.globus_demo_staples import (
        build_globus_demo_staples_catalog,
    )

    catalog, listings = build_globus_demo_staples_catalog()
    provider_factory = GlobusCatalogProviderFactory(catalog, listings)
    planner = DemandScopedPlanApplicationService(
        catalog,
        provider_factory,
        clock=utc_now,
    )
    return _build_web_app(
        data_dir,
        catalog=catalog,
        planner=planner,
        allow_remote_hosts=allow_remote_hosts,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Household Supply Planner M11 local web MVP")
    parser.add_argument("--serve", action="store_true", help="run the local HTTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument(
        "--live-globus",
        action="store_true",
        help="use the real M5.1 Globus catalog and live request-scoped acquisition",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="explicitly allow binding the unauthenticated demo to a non-loopback host",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.serve:
        profile = "m11-globus" if args.live_globus else "m11-demo"
        data_dir = args.data_dir or (Path.home() / ".household-supply-planner" / profile)
        data_dir.mkdir(parents=True, exist_ok=True)
        app = (
            build_live_globus_app(data_dir, allow_remote_hosts=args.allow_remote)
            if args.live_globus
            else build_demo_app(data_dir, allow_remote_hosts=args.allow_remote)
        )
        print("M11 local web MVP")
        print(f"  data: {data_dir.resolve()}")
        print(f"  open: http://{args.host}:{args.port}/")
        if args.live_globus:
            print("  market: live Globus, scoped to Items demanded by each plan")
        else:
            print("  market: offline fixture data; planner/household semantics are real")
        serve_local_web(
            app,
            host=args.host,
            port=args.port,
            allow_remote=args.allow_remote,
        )
        return

    with TemporaryDirectory(prefix="household-m11-") as directory:
        app = build_demo_app(Path(directory))
        catalog_payload = app.api.handle("GET", "/catalog").body["catalog"]
        assert len(catalog_payload["items"]) == 3
        assert len(catalog_payload["skus"]) == 3
        assert app.api.handle("GET", "/household/state").status == 200
        assert app.api.handle("GET", "/plans?limit=5").status == 200
        print("M11 local web MVP")
        print("  app assembly: ok")
        print("  catalog items: 3")
        print("  household API: ok")
        print("  plan history API: ok")
        print("  serve with: python examples/m11_local_web.py --serve")


if __name__ == "__main__":
    main()
