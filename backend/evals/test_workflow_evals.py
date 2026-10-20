import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from app.discovery import _gemini_criteria_proposal
from app import main
from app.main import Applicant, LoanProduct, Match, _match_reasons, _normalized_segment, _render_notification, validate_row


ROOT = Path(__file__).resolve().parents[2]


def _proposal_response(rules=None, evidence=None):
    result = {
        "rules": rules or [{
            "segment": "salaried",
            "min_age": 21,
            "max_age": 60,
            "minimum_monthly_income_inr": 25000,
            "total_work_experience_years": 2,
            "credit_score_preferred": 700,
        }],
        "evidence": evidence or [
            {"field": "salaried.min_age", "quote": "Age 21 to 60 years"},
            {"field": "salaried.max_age", "quote": "Age 21 to 60 years"},
            {"field": "salaried.minimum_monthly_income_inr", "quote": "Minimum monthly income INR 25,000"},
            {"field": "salaried.total_work_experience_years", "quote": "Total work experience: 2 years"},
            {"field": "salaried.credit_score_preferred", "quote": "Credit score 700 and above is preferred"},
        ],
        "uncertainties": ["Employer category must be confirmed."],
    }
    return {
        "candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}],
        "usageMetadata": {"promptTokenCount": 22, "candidatesTokenCount": 16, "totalTokenCount": 38},
    }


# Workflow A evaluation: deterministic safety checks around model proposals.
def test_discovery_proposal_requires_exact_source_quotes_and_records_model(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "local-test-key")
    monkeypatch.setenv("LLM_MODEL", "gemini-flash-latest")
    page_text = (
        "Age 21 to 60 years. Minimum monthly income INR 25,000. "
        "Total work experience: 2 years. Credit score 700 and above is preferred."
    )
    response = Mock(ok=True)
    response.json.return_value = _proposal_response()
    request = Mock(return_value=response)
    monkeypatch.setattr("app.discovery.requests.post", request)
    proposal = _gemini_criteria_proposal(
        {"lender": "Example Bank", "product_name": "Personal Loan"},
        {"url": "https://lender.example/eligibility", "text": page_text, "sha256": "a" * 64},
        {},
    )
    assert proposal["status"] == "pending_human_review"
    assert proposal["model"] == "gemini-flash-latest"
    assert len(proposal["evidence"]) == 5
    assert proposal["usage"]["totalTokenCount"] == 38
    sent_prompt = request.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
    assert "Alex Example" not in sent_prompt
    assert "@example.com" not in sent_prompt


def test_discovery_proposal_rejects_a_numeric_rule_not_in_its_quote(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "local-test-key")
    monkeypatch.setenv("LLM_MODEL", "gemini-flash-latest")
    result = _proposal_response(
        rules=[{"segment": "salaried", "minimum_monthly_income_inr": 99999}],
        evidence=[{"field": "salaried.minimum_monthly_income_inr", "quote": "Minimum monthly income INR 25,000"}],
    )
    response = Mock(ok=True)
    response.json.return_value = result
    monkeypatch.setattr("app.discovery.requests.post", Mock(return_value=response))
    proposal = _gemini_criteria_proposal(
        {"lender": "Example Bank", "product_name": "Personal Loan"},
        {"url": "https://lender.example/eligibility", "text": "Minimum monthly income INR 25,000", "sha256": "b" * 64},
        {},
    )
    assert proposal["status"] == "failed"
    assert "validated" in proposal["error"]


def test_discovery_does_not_call_model_without_a_configured_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    request = Mock()
    monkeypatch.setattr("app.discovery.requests.post", request)
    result = _gemini_criteria_proposal(
        {"lender": "Example Bank", "product_name": "Personal Loan"},
        {"url": "https://lender.example/eligibility", "text": "Unstructured source", "sha256": "c" * 64},
        {},
    )
    assert result is None
    request.assert_not_called()


# Workflow B evaluation: only structured fields determine rules; missing facts stay unresolved.
def test_matching_rule_classification_keeps_unknown_facts_visible():
    reasons, status = _match_reasons("HDFC Bank", {
        "minimum_monthly_income_inr": 25000,
        "total_work_experience_years": 2,
    })
    assert status == "needs_information"
    assert any("gross income" in reason for reason in reasons)
    assert any("work experience" in reason for reason in reasons)


def test_matching_classifies_only_known_rules_as_potential():
    reasons, status = _match_reasons("Example Bank", {"min_age": 21, "max_age": 60})
    assert status == "potential"
    assert all("not lender approval" in reason.lower() for reason in reasons[:1])


def test_ingestion_normalizes_employment_and_rejects_bad_score():
    assert _normalized_segment("Full-Time Salaried") == "salaried"
    row, error = validate_row({
        "user_id": "u-1", "name": "Sample", "email": "sample@example.test",
        "monthly_income": "50000", "credit_score": "1001",
        "employment_status": "Salaried", "age": "30",
    }, 2)
    assert row is None
    assert "credit_score" in error


# Workflow C evaluation: all applicant delivery is redirected to the controlled inbox.
def test_notification_message_never_uses_applicant_email(monkeypatch):
    monkeypatch.setattr(main, "NOTIFICATION_TEST_RECIPIENT", "test@localhost")
    monkeypatch.setattr(main, "ZoneInfo", lambda _: timezone.utc)
    applicant = Applicant(
        id=10, batch_id="batch-1", external_user_id="user-1", name="Sample Person",
        email="person@example.test", monthly_income=50000, credit_score=740,
        employment_status="Salaried", employment_segment="salaried", age=30,
    )
    product = LoanProduct(
        id=1, lender="Example Bank", product_name="Personal Loan",
        product_url="https://lender.example/product", rate_url="https://lender.example/rates",
        eligibility_url="https://lender.example/eligibility", rate_min=10.0, rate_max=18.0,
        last_checked_at=datetime.now(timezone.utc),
    )
    match = Match(id=30, applicant_id=10, product_id=1, batch_id="batch-1", reasons=["Confirm work tenure."], criteria_version=1)
    message = _render_notification(applicant, [(product, match)], "claim-1", "batch-1")
    assert message["to"] == "test@localhost"
    assert "person@example.test" not in message["text"]
    assert "not an approval or offer" in message["text"]
    assert message["claim_id"] == "claim-1"


# Workflow topology evaluations catch stale exports before import into n8n.
def _workflow(name):
    return json.loads((ROOT / "workflows" / name).read_text(encoding="utf-8"))


def _assert_connections_resolve(workflow):
    node_names = {node["name"] for node in workflow["nodes"]}
    for source, groups in workflow["connections"].items():
        assert source in node_names
        for group in groups.values():
            for edges in group:
                for edge in edges:
                    assert edge["node"] in node_names


def test_workflow_a_discovery_routes_proposals_to_review_queue():
    workflow = _workflow("product-discovery.json")
    _assert_connections_resolve(workflow)
    assert workflow["connections"]["Daily at 07:00"]["main"][0][0]["node"] == "Fetch and validate official lender pages"
    assert workflow["connections"]["Fetch and validate official lender pages"]["main"][0][0]["node"] == "Load Gemini criteria proposals for human review"


def test_workflow_b_matching_runs_before_notifications():
    workflow = _workflow("user-loan-matching.json")
    _assert_connections_resolve(workflow)
    gate = "Authorize internal batch trigger"
    assert workflow["connections"]["Upload batch webhook"]["main"][0][0]["node"] == gate
    assert workflow["connections"][gate]["main"][0][0]["node"] == "Set-based SQL matching and bulk persistence"


def test_workflow_c_has_empty_page_guard_and_bounded_continuation():
    workflow = _workflow("user-notifications.json")
    _assert_connections_resolve(workflow)
    names = {node["name"] for node in workflow["nodes"]}
    assert "Send through local Mailpit SMTP" in names
    assert "Mark test delivery complete" in names
    assert "Continue next notification page" in names
    assert any(node["name"] == "One email per applicant" and "continue_after_page" in node["parameters"].get("jsCode", "") for node in workflow["nodes"])


def test_all_workflow_api_calls_use_runtime_service_token_and_webhooks_check_it():
    expected_gates = {
        "user-loan-matching.json": "Authorize internal batch trigger",
        "user-notifications.json": "Authorize internal notification trigger",
    }
    for filename in ("product-discovery.json", *expected_gates):
        workflow = _workflow(filename)
        for node in workflow["nodes"]:
            params = node.get("parameters", {})
            url = str(params.get("url", ""))
            is_internal = "api:8000" in url or node["name"] in {"Start notification workflow", "Continue next notification page"}
            if is_internal:
                headers = params.get("headerParameters", {}).get("parameters", [])
                service_header = next((h for h in headers if h.get("name", "").lower() == "x-workflow-token"), None)
                assert service_header and "$env.N8N_SERVICE_TOKEN" in service_header.get("value", "")
                assert "N8N_SERVICE_TOKEN" not in service_header.get("value", "").replace("$env.N8N_SERVICE_TOKEN", "")
        gate_name = expected_gates.get(filename)
        if gate_name:
            gate_node = next(node for node in workflow["nodes"] if node["name"] == gate_name)
            condition = gate_node["parameters"]["conditions"]["conditions"][0]
            assert "x-workflow-token" in condition["leftValue"]
            assert "$env.N8N_SERVICE_TOKEN" in condition["rightValue"]
