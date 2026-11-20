"""Pull title, price, currency and stock status out of a product page.

Extractors are tried in order and the first one that finds a price wins:

1. A site-specific adapter, when one exists for the page's domain.
2. schema.org ``Product`` data in JSON-LD, which most stores publish for search engines.
3. Product meta tags (Open Graph ``product:price:amount``) and ``itemprop="price"`` microdata.

Adding a store that has none of these means writing one small adapter function.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class ProductData:
    title: str | None
    price: Decimal
    currency: str | None
    in_stock: bool | None  # None when the page does not say
    method: str  # which extractor found it, for debugging


class ExtractionError(ValueError):
    pass


CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR", "C$": "CAD", "A$": "AUD"}


def parse_price(text: str | int | float | Decimal | None) -> Decimal | None:
    """Turn '£51.77', '$1,299.00', '1.299,00 €' or 19.5 into a Decimal."""
    if text is None:
        return None
    if isinstance(text, (int, float, Decimal)):
        return Decimal(str(text)).quantize(Decimal("0.01"))
    match = re.search(r"\d[\d.,\s ]*", str(text))
    if not match:
        return None
    number = re.sub(r"[\s ]", "", match.group(0)).rstrip(".,")
    if "," in number and "." in number:
        # Whichever separator comes last is the decimal point.
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        head, _, tail = number.rpartition(",")
        # '12,99' is a decimal comma; '1,299' is a thousands separator.
        number = f"{head.replace(',', '')}.{tail}" if len(tail) != 3 else number.replace(",", "")
    elif number.count(".") > 1:
        number = number.replace(".", "")  # '1.299.000' style thousands
    try:
        return Decimal(number).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def detect_currency(text: str | None) -> str | None:
    if not text:
        return None
    code = re.search(r"\b[A-Z]{3}\b", text)
    if code:
        return code.group(0)
    for symbol in sorted(CURRENCY_SYMBOLS, key=len, reverse=True):
        if symbol in text:
            return CURRENCY_SYMBOLS[symbol]
    return None


def parse_availability(value: str | None) -> bool | None:
    if not value:
        return None
    v = value.lower().rsplit("/", 1)[-1].replace(" ", "")
    if any(k in v for k in ("outofstock", "soldout", "discontinued", "unavailable")):
        return False
    if any(k in v for k in ("instock", "limitedavailability", "onlineonly", "instoreonly", "preorder", "available")):
        return True
    return None


# -- site adapters --------------------------------------------------------------


def _books_toscrape(soup: BeautifulSoup) -> ProductData | None:
    main = soup.select_one(".product_main")
    if main is None:
        return None
    price_text = main.select_one(".price_color")
    price = parse_price(price_text.get_text()) if price_text else None
    if price is None:
        return None
    availability = main.select_one(".availability")
    stock_text = availability.get_text(" ", strip=True) if availability else ""
    title = main.find("h1")
    return ProductData(
        title=title.get_text(strip=True) if title else None,
        price=price,
        currency=detect_currency(price_text.get_text()),
        in_stock=parse_availability(stock_text.replace(" ", "")) if stock_text else None,
        method="adapter:books.toscrape.com",
    )


SITE_ADAPTERS: dict[str, Callable[[BeautifulSoup], ProductData | None]] = {
    "books.toscrape.com": _books_toscrape,
}


# -- generic extractors ---------------------------------------------------------


def _iter_jsonld(soup: BeautifulSoup) -> Iterator[dict]:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                for key in ("@graph", "mainEntity", "hasVariant"):
                    if key in node:
                        stack.append(node[key])


def _is_product(node: dict) -> bool:
    types = node.get("@type", [])
    types = [types] if isinstance(types, str) else types
    return any(t in ("Product", "ProductGroup", "IndividualProduct") for t in types)


def _from_jsonld(soup: BeautifulSoup) -> ProductData | None:
    for node in _iter_jsonld(soup):
        if not _is_product(node):
            continue
        offers = node.get("offers")
        offers = offers if isinstance(offers, list) else [offers] if offers else []
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            price = offer.get("price")
            if price is None:
                price = offer.get("lowPrice")  # AggregateOffer
            if price is None and isinstance(offer.get("priceSpecification"), dict):
                price = offer["priceSpecification"].get("price")
            price = parse_price(price)
            if price is None:
                continue
            name = node.get("name")
            return ProductData(
                title=name.strip() if isinstance(name, str) else None,
                price=price,
                currency=offer.get("priceCurrency"),
                in_stock=parse_availability(offer.get("availability")),
                method="json-ld",
            )
    return None


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _from_meta(soup: BeautifulSoup) -> ProductData | None:
    price_text = _meta(soup, "product:price:amount", "og:price:amount")
    currency = _meta(soup, "product:price:currency", "og:price:currency")
    availability = _meta(soup, "product:availability", "og:availability")
    method = "meta"

    if price_text is None:
        item = soup.find(attrs={"itemprop": "price"})
        if item is None:
            return None
        price_text = item.get("content") or item.get_text()
        cur = soup.find(attrs={"itemprop": "priceCurrency"})
        currency = currency or (cur.get("content") if cur else None) or detect_currency(item.get_text())
        avail = soup.find(attrs={"itemprop": "availability"})
        availability = availability or (avail.get("href") or avail.get("content") if avail else None)
        method = "microdata"

    price = parse_price(price_text)
    if price is None:
        return None
    title = _meta(soup, "og:title") or (soup.title.get_text(strip=True) if soup.title else None)
    return ProductData(
        title=title,
        price=price,
        currency=currency,
        in_stock=parse_availability(availability),
        method=method,
    )


def extract(html: str, url: str) -> ProductData:
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(url).netloc.lower().removeprefix("www.")
    extractors = []
    if host in SITE_ADAPTERS:
        extractors.append(SITE_ADAPTERS[host])
    extractors += [_from_jsonld, _from_meta]
    for extractor in extractors:
        result = extractor(soup)
        if result is not None:
            return result
    raise ExtractionError(
        "No price found (no site adapter, JSON-LD Product or price meta tags). "
        "The page may load prices with JavaScript, or need a site adapter."
    )
