#!/usr/bin/env python3
"""
Zoek uit wat een webshop wél doorlaat.

    python diagnose.py https://catchyourcards.nl

Test een reeks endpoints met browser-achtige headers en print per URL de
statuscode, de webserver, of Cloudflare in de weg zit, en het begin van de
response. Daarmee weten we of het een Cloudflare-blokkade is (dan is er iets
aan te doen) of een dichtgezette API (dan moeten we HTML scrapen).
"""
import sys
import requests

BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="126", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}

PATHS = [
    "/",
    "/winkel/",
    "/wp-json/wc/store/v1/products?per_page=5",
    "/wp-json/wc/store/products?per_page=5",
    "/?rest_route=/wc/store/v1/products&per_page=5",
    "/products.json?limit=5",
    "/sitemap_index.xml",
    "/product-sitemap.xml",
    "/feed/",
    "/robots.txt",
]


def check(session, url, referer=None):
    headers = dict(BROWSER)
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    if "json" in url or "rest_route" in url:
        headers["Accept"] = "application/json, text/plain, */*"
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["X-Requested-With"] = "XMLHttpRequest"

    try:
        r = session.get(url, headers=headers, timeout=25, allow_redirects=True)
    except requests.RequestException as e:
        print(f"  ✗ netwerkfout: {type(e).__name__}: {str(e)[:120]}")
        return None

    server = r.headers.get("server", "?")
    cf = "cf-ray" in {k.lower() for k in r.headers}
    ctype = r.headers.get("content-type", "?").split(";")[0]
    body = r.text[:160].replace("\n", " ").replace("\r", " ")

    mark = "✓" if r.status_code == 200 else "✗"
    print(f"  {mark} HTTP {r.status_code} | {ctype} | {len(r.content)} bytes | "
          f"server={server} | cloudflare={'ja' if cf else 'nee'}")
    if r.status_code != 200 or "json" in ctype:
        print(f"      {body}")
    return r


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    base = sys.argv[1].rstrip("/")
    session = requests.Session()

    print(f"\n=== {base} ===\n")
    print("Stap 1: homepage bezoeken (cookies ophalen)")
    home = check(session, base + "/")
    print(f"  cookies: {list(session.cookies.keys()) or 'geen'}\n")

    print("Stap 2: endpoints testen mét die cookies en een Referer\n")
    for path in PATHS[1:]:
        print(f"{path}")
        check(session, base + path, referer=base + "/")
        print()

    print("Klaar. Stuur deze hele uitvoer terug.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
