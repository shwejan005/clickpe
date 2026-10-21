"""Shared CSV applicant validation used by the API and AWS ingestion Lambda."""

from __future__ import annotations

from typing import Any


def normalized_segment(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if "self" in normalized or "business" in normalized:
        return "self-employed"
    if any(term in normalized for term in ("unemploy", "student", "retired")):
        return normalized
    if "salary" in normalized or "salaried" in normalized or "employ" in normalized:
        return "salaried"
    return normalized


def validate_row(row: dict[str, str | None], row_number: int) -> tuple[dict[str, Any] | None, str | None]:
    try:
        user_id = (row.get("user_id") or "").strip()
        name = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip()
        employment_status = (row.get("employment_status") or "").strip()
        if not all((user_id, name, email, employment_status)):
            raise ValueError("user_id, name, email, and employment_status are required")
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            raise ValueError("email is not valid")
        monthly_income = float(row.get("monthly_income", ""))
        credit_score = int(row.get("credit_score", ""))
        age = int(row.get("age", ""))
        if monthly_income <= 0:
            raise ValueError("monthly_income must be greater than zero")
        if not 0 <= credit_score <= 1000:
            raise ValueError("credit_score must be between 0 and 1000")
        if not 18 <= age <= 100:
            raise ValueError("age must be between 18 and 100")
        return ({
            "external_user_id": user_id,
            "name": name,
            "email": email,
            "monthly_income": monthly_income,
            "credit_score": credit_score,
            "employment_status": employment_status,
            "employment_segment": normalized_segment(employment_status),
            "age": age,
        }, None)
    except (TypeError, ValueError) as exc:
        return None, f"Row {row_number}: {exc}"
