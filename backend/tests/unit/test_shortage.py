from decimal import Decimal as D

from app.domain.shortage import calculate_count, shortage_rate, shortage_reduction


def test_count_shortage_and_surplus() -> None:
    assert calculate_count(D(25), D(22)).difference == D(-3)
    assert calculate_count(D(25), D(22)).shortage == D(3)
    assert calculate_count(D(22), D(24)).difference == D(2)
    assert calculate_count(D(22), D(24)).shortage == D(0)


def test_rate_and_unavailable_denominator() -> None:
    result = shortage_rate(D(20), D(10), D(2), D(3))
    assert result.status == "CALCULABLE"
    assert result.value == D("9.375")
    assert shortage_rate(D(0), D(0), D(0), D(0)).value is None
    assert shortage_rate(None, D(10), D(0), D(3)).status == "NOT_CALCULABLE"


def test_reduction_and_missing_baseline() -> None:
    assert shortage_reduction(D(15), D(12)).value == D(20)
    assert shortage_reduction(D(0), D(12)).status == "NOT_VERIFIABLE"
    assert shortage_reduction(None, D(12)).value is None
