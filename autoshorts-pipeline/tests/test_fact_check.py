from src.fact_check import is_authoritative


def test_authoritative_domain_matching_is_not_any_dot_com():
    assert is_authoritative("https://science.nasa.gov/example")
    assert is_authoritative("https://example.edu/research")
    assert not is_authoritative("https://random-shop.com/science")
