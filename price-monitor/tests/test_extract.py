from decimal import Decimal

import pytest

from price_monitor.extract import ExtractionError, extract, parse_availability, parse_price


@pytest.mark.parametrize(
    "text, expected",
    [
        ("£51.77", "51.77"),
        ("$1,299.00", "1299.00"),
        ("1.299,00 €", "1299.00"),
        ("12,99", "12.99"),
        ("1,299", "1299.00"),
        ("Now only USD 7", "7.00"),
        ("1 049,50 kr", "1049.50"),
        ("1.299.000", "1299000.00"),
        (19.5, "19.50"),
        ("Free", None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == (Decimal(expected) if expected else None)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://schema.org/InStock", True),
        ("http://schema.org/OutOfStock", False),
        ("in stock", True),
        ("Sold out", False),
        ("PreOrder", True),
        ("", None),
        ("call us", None),
    ],
)
def test_parse_availability(value, expected):
    assert parse_availability(value) is expected


def test_books_toscrape_adapter(fixture_html):
    data = extract(fixture_html("books_product.html"), "https://books.toscrape.com/catalogue/x/index.html")
    assert data.title == "A Light in the Attic"
    assert data.price == Decimal("51.77")
    assert data.currency == "GBP"
    assert data.in_stock is True
    assert data.method == "adapter:books.toscrape.com"


def test_jsonld_graph_with_broken_block(fixture_html):
    data = extract(fixture_html("jsonld_product.html"), "https://shop.example.com/p/1")
    assert (data.title, data.price, data.currency, data.in_stock) == (
        "Trail Running Shoe", Decimal("1299.00"), "USD", False)
    assert data.method == "json-ld"


def test_jsonld_aggregate_offer(fixture_html):
    data = extract(fixture_html("aggregate_offer.html"), "https://shop.example.com/lamp")
    assert (data.price, data.currency, data.in_stock) == (Decimal("24.50"), "EUR", True)


def test_meta_tags(fixture_html):
    data = extract(fixture_html("meta_product.html"), "https://shop.example.com/mug")
    assert (data.title, data.price, data.currency, data.in_stock, data.method) == (
        "Ceramic Mug", Decimal("12.99"), "EUR", True, "meta")


def test_microdata(fixture_html):
    data = extract(fixture_html("microdata_product.html"), "https://shop.example.com/hose")
    assert (data.price, data.currency, data.in_stock, data.method) == (
        Decimal("34.99"), "USD", True, "microdata")


def test_no_price_raises(fixture_html):
    with pytest.raises(ExtractionError, match="JavaScript"):
        extract(fixture_html("no_price.html"), "https://shop.example.com/spa")
