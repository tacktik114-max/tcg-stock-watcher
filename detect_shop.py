#!/usr/bin/env python3
"""
Zoek uit welke adapter een webshop nodig heeft.

    python detect_shop.py https://ceescards.eu

Print of de shop WooCommerce of Shopify is, hoeveel producten er te vinden
zijn en een kant-en-klaar config-blok dat je in config.yml kunt plakken.
"""
import sys
import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "nl-NL,nl;q=0.9",
}

TESTS = [
    ("woocommerce", "/wp-json/wc/store/v1/products?per_page=5"),
    ("shopify", "/products.json?limit=5"),
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    base = sys.argv[1].rstrip("/")

    for adapter, path in TESTS:
        url = base + path
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"✗ {adapter:12} netwerkfout: {e}")
            continue

        if r.status_code != 200:
            print(f"✗ {adapter:12} HTTP {r.status_code}")
            continue

        try:
            data = r.json()
        except ValueError:
            print(f"✗ {adapter:12} geen JSON terug (waarschijnlijk een HTML-pagina)")
            continue

        items = data if isinstance(data, list) else data.get("products", [])
        if not items:
            print(f"✗ {adapter:12} JSON maar geen producten")
            continue

        print(f"✓ {adapter} werkt — voorbeeld: {items[0].get('name') or items[0].get('title')}")
        print("\nPlak dit in config.yml onder 'sites:':\n")
        print(f"""  - name: {base.split('//')[-1].split('.')[0].title()}
    enabled: true
    adapter: {adapter}
    base_url: {base}
    max_pages: 10
    include: ["pokémon", "pokemon"]
    exclude: ["sleeve", "toploader"]
""")
        return 0

    print("\nGeen publieke API gevonden. Gebruik de 'html'-adapter met CSS-selectors,")
    print("of stuur me de URL van de categoriepagina dan zoek ik de selectors uit.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
