"""URL validation: what ORBIT is willing to hand to the operating system."""

from __future__ import annotations

import pytest

from core.urls import normalise_url, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "https://example.com/path?q=1#x",
        "http://localhost:8000",
        "mailto:someone@example.com",
        "ms-settings:display",
        "file:///C:/Users",
    ],
)
def test_allowed_urls(url: str) -> None:
    assert validate_url(url) is None


@pytest.mark.parametrize(
    "url,needle",
    [
        ("", "website address"),
        ("   ", "website address"),
        ("example.com", "Add a scheme"),
        ("ftp://example.com", "not allowed"),
        ("javascript:alert(1)", "not allowed"),
        ("https://a b.com", "spaces"),
        ("https://", "missing a domain"),
        ("mailto:", "email address"),
    ],
)
def test_rejected_urls_explain_why(url: str, needle: str) -> None:
    problem = validate_url(url)
    assert problem is not None
    assert needle.lower() in problem.lower()


def test_normalise_trims_whitespace() -> None:
    assert normalise_url("  https://example.com  ") == "https://example.com"
