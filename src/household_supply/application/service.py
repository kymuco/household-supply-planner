from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable

from household_supply.domain import CatalogSnapshot
from household_supply.market import (
    MarketCompilationPolicy,
    MarketProvider,
    acquire_market,
    compile_market_snapshot,
)
from household_supply.planning import build_multi_objective_plan

from .models import (
    ApplicationPlanRequest,
    ApplicationPlanResult,
    build_application_problem,
    validate_application_request_catalog,
)


Clock = Callable[[], datetime]


@runtime_checkable
class ApplicationPlanner(Protocol):
    """Planning authority consumed by lifecycle/replenishment orchestration."""

    @property
    def catalog(self) -> CatalogSnapshot: ...

    def plan(self, request: ApplicationPlanRequest) -> ApplicationPlanResult: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


class ApplicationMarketError(RuntimeError):
    """The configured market providers could not produce an admissible market basis."""

@dataclass(frozen=True, slots=True)
class PlanApplicationService:
    catalog: CatalogSnapshot
    providers: tuple[MarketProvider, ...]
    market_policy: MarketCompilationPolicy = MarketCompilationPolicy()
    clock: Clock = _utc_now

    def __post_init__(self) -> None:
        providers = tuple(self.providers)
        if not providers:
            raise ValueError("application service requires at least one market provider")
        if not callable(self.clock):
            raise TypeError("application service clock must be callable")

        provider_ids = []
        for provider in providers:
            provider_id = provider.provider_id.strip()
            if not provider_id:
                raise ValueError("application market provider_id must not be empty")
            provider_ids.append(provider_id)
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("application service contains duplicate provider_id values")

        object.__setattr__(self, "providers", providers)

    def plan(self, request: ApplicationPlanRequest) -> ApplicationPlanResult:
        validate_application_request_catalog(request, self.catalog)

        batches = []
        for provider in self.providers:
            try:
                batches.append(acquire_market(provider))
            except Exception as exc:
                raise ApplicationMarketError(
                    f"market acquisition failed for provider {provider.provider_id!r}"
                ) from exc

        captured_at = self.clock()
        _require_aware(captured_at, label="application capture time")
        latest_acquisition = max(batch.acquired_at for batch in batches)
        if captured_at < latest_acquisition:
            raise RuntimeError(
                "application capture time precedes acquired market evidence"
            )

        compilation = compile_market_snapshot(
            self.catalog,
            tuple(batches),
            captured_at=captured_at,
            policy=self.market_policy,
        )

        # Provider evidence must remain unchanged. Without an attributable FX
        # quote, a price in another currency cannot satisfy this budget.
        foreign_currencies = sorted({
            offer.price.currency
            for offer in compilation.snapshot.offers
            if offer.price.currency != request.budget.currency
        })
        if foreign_currencies:
            raise ApplicationMarketError(
                "market offers require explicit FX conversion before planning: "
                + ", ".join(foreign_currencies)
                + f" != {request.budget.currency}"
            )

        problem = build_application_problem(request, compilation)
        objective_policy = request.effective_objective_policy()
        plan = build_multi_objective_plan(problem, objective_policy)
        return ApplicationPlanResult(
            request=request,
            market_compilation=compilation,
            problem=problem,
            objective_policy=objective_policy,
            plan=plan,
        )
