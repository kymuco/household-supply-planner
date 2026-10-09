from __future__ import annotations

from io import StringIO
import json

from household_supply.application import (
    PlanJsonApi,
    parse_plan_payload,
    run_plan_cli,
    serialize_plan_result,
)
from household_supply.domain import Money

from test_application_service import make_service, make_request


def request_payload() -> dict:
    return {
        "budget": {"amount": "1000", "currency": "KGS"},
        "demands": [
            {"item_id": "milk", "quantity": {"amount": "1500", "unit": "ml"}},
            {"item_id": "oil", "quantity": {"amount": "500", "unit": "ml"}},
        ],
        "inventory": [
            {
                "lot_id": "milk-open",
                "item_id": "milk",
                "quantity": {"amount": "500", "unit": "ml"},
            }
        ],
    }


def test_json_payload_parses_to_typed_request() -> None:
    request = parse_plan_payload(request_payload())
    assert request == make_request()


def test_json_payload_rejects_unknown_fields() -> None:
    payload = request_payload()
    payload["budegt"] = payload["budget"]
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 422
    assert response.body["error"] == "invalid_request"
    assert "unknown fields" in response.body["detail"]


def test_json_payload_rejects_float_money() -> None:
    payload = request_payload()
    payload["budget"]["amount"] = 1000.5
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 422
    assert "float is not accepted" in response.body["detail"]


def test_json_api_rejects_unquoted_fx_budget() -> None:
    payload = request_payload()
    payload["budget"]["currency"] = "KZT"
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 502
    assert response.body["error"] == "market_unavailable"
    assert "explicit FX conversion" in response.body["detail"]


def test_json_api_returns_plan_and_market_summary() -> None:
    response = PlanJsonApi(make_service()).handle("POST", "/plans", request_payload())
    assert response.status == 200
    assert response.body["status"] == "feasible"
    assert response.body["total_cost"] == {"amount": "310", "currency": "KGS"}
    assert response.body["market"]["offer_count"] == 2
    assert response.body["market"]["dispositions"]["accepted"] == 2
    assert response.body["market"]["dispositions"]["stale"] == 0
    assert [row["sku_id"] for row in response.body["purchases"]] == [
        "milk-1l",
        "oil-1l",
    ]
    assert response.body["purchases"][0]["source_ref"] == "fixture://milk"


def test_json_api_health_and_route_semantics() -> None:
    api = PlanJsonApi(make_service())
    assert api.handle("GET", "/health").status == 200
    assert api.handle("POST", "/health", {}).status == 405
    assert api.handle("GET", "/plans").status == 405
    assert api.handle("GET", "/missing").status == 404


def test_json_result_serialization_is_deterministic() -> None:
    result = make_service().plan(make_request())
    assert serialize_plan_result(result) == serialize_plan_result(result)


def test_json_api_returns_infeasible_as_normal_200_result() -> None:
    payload = request_payload()
    payload["budget"] = {"amount": "1", "currency": "KGS"}
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 200
    assert response.body["status"] == "infeasible"
    assert response.body["infeasibility_reasons"]


def test_cli_reads_stdin_and_writes_json_plan() -> None:
    stdin = StringIO(json.dumps(request_payload()))
    stdout = StringIO()
    stderr = StringIO()
    code = run_plan_cli(
        make_service(),
        ["plan", "--request", "-"],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 0
    assert stderr.getvalue() == ""
    body = json.loads(stdout.getvalue())
    assert body["status"] == "feasible"
    assert body["total_cost"] == {"amount": "310", "currency": "KGS"}


def test_cli_returns_input_error_without_traceback() -> None:
    stdout = StringIO()
    stderr = StringIO()
    code = run_plan_cli(
        make_service(),
        ["plan", "--request", "-"],
        stdin=StringIO("not json"),
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 2
    assert stdout.getvalue() == ""
    assert "invalid_request" in stderr.getvalue()


def test_json_payload_rejects_non_string_currency() -> None:
    payload = request_payload()
    payload["budget"]["currency"] = None
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 422
    assert "JSON string" in response.body["detail"]


def test_json_payload_rejects_non_string_item_id() -> None:
    payload = request_payload()
    payload["demands"][0]["item_id"] = 123
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 422
    assert "JSON string" in response.body["detail"]


def test_json_payload_rejects_duplicate_surplus_penalties() -> None:
    payload = request_payload()
    payload["objective"] = {
        "additional_store_penalty": {"amount": "0", "currency": "KGS"},
        "surplus_penalties": [
            {"item_id": "milk", "cost_per_base_unit": {"amount": "0.01", "currency": "KGS"}},
            {"item_id": "milk", "cost_per_base_unit": {"amount": "0.02", "currency": "KGS"}},
        ],
    }
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 422
    assert "duplicate surplus" in response.body["detail"]


def test_json_payload_rejects_negative_objective_penalty() -> None:
    payload = request_payload()
    payload["objective"] = {
        "additional_store_penalty": {"amount": "-1", "currency": "KGS"}
    }
    response = PlanJsonApi(make_service()).handle("POST", "/plans", payload)
    assert response.status == 422
    assert "negative" in response.body["detail"]


def test_json_api_maps_provider_failure_to_502() -> None:
    from household_supply.application import PlanApplicationService

    service = make_service()

    class FailingProvider:
        provider_id = "fixture"

        def acquire(self):
            raise RuntimeError("offline")

    api = PlanJsonApi(
        PlanApplicationService(service.catalog, (FailingProvider(),), clock=lambda: service.clock())
    )
    response = api.handle("POST", "/plans", request_payload())
    assert response.status == 502
    assert response.body["error"] == "market_unavailable"
