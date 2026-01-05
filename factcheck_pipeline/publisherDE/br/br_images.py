#br_images.py — hybrid: requests for HTML/images, Playwright only for embed screenshots
import os, re, time, hashlib
from urllib.parse import urlsplit, parse_qs, urljoin
import json

import requests
from bs4 import BeautifulSoup
import pandas as pd

# Only import Playwright when needed for screenshots
_playwright_available = True
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    _playwright_available = False

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")

SESSION_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

CONSENT_TEXTS = [
    "Alle akzeptieren", "Alle Cookies akzeptieren",
    "Zustimmen", "Einverstanden", "Akzeptieren",
    "Reject all", "Accept all", "OK", "Agree",
]


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def get_ext(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]

def ext_priority(u: str) -> int:
    e = get_ext(u)
    if e in (".jpg", ".jpeg"): return 4
    if e == ".png": return 3
    if e == ".webp": return 2
    if e == ".avif": return 1
    return 0

def infer_width_from_url(u: str) -> int:
    qs = parse_qs(urlsplit(u).query)
    if "w" in qs:
        try:
            return int(qs["w"][0])
        except (ValueError, IndexError):
            pass
    m = (
        re.search(r"/fit-in/(\d+)x", u) or
        re.search(r"/(\d{3,4})x\d{2,4}/", u) or
        re.search(r"[\W_](\d{3,4})w(?:[\W_]|$)", u)
    )
    return int(m.group(1)) if m else 0

def canonical_key(u: str) -> str:
    parts = urlsplit(u)
    path = parts.path
    q = parse_qs(parts.query)
    q.pop("w", None)
    stable_query = "&".join(f"{k}={v[0]}" for k, v in sorted(q.items()) if v)
    path = re.sub(r"/fit-in/\d+x\d+/?", "/", path, flags=re.I)
    path = re.sub(r"/\d{2,4}x\d{2,4}/", "/", path, flags=re.I)
    dirpath, fname = os.path.split(path)
    if fname:
        fname = re.sub(r"-(\d{2,4}x\d{2,4})(\.[a-z0-9]{2,4}$)", r"\2", fname, flags=re.I)
        fname = re.sub(r"-(scaled)(\.[a-z0-9]{2,4}$)", r"\2", fname, flags=re.I)
        path = (dirpath + "/" + fname).replace("//", "/")
    return f"{parts.netloc}{path}?{stable_query}" if stable_query else f"{parts.netloc}{path}"

def caption_key(s: str) -> str:
    return clean(s).lower()

def safe_slug(s: str, n=64) -> str:
    s = re.sub(r"https?://", "", s or "")
    s = re.sub(r"[^\w.-]+", "_", s).strip("._")
    return s[-n:] if len(s) > n else (s or "article")


def ok_ext(u: str) -> bool:
    if not u:
        return False
    path = urlsplit(u).path.lower().split("?")[0]
    return any(path.endswith(e) for e in IMG_EXT)


def parse_srcset(srcset_str: str, base_url: str) -> list[dict]:
    """Parse srcset attribute into list of {url, w}."""
    results = []
    if not srcset_str:
        return results
    for part in srcset_str.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        raw_url = bits[0]
        url = urljoin(base_url, raw_url)
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                pass
        if not w:
            w = infer_width_from_url(url)
        results.append({"url": url, "w": w})
    return results


# ---------------------------------------------------------------------------
# Phase 1: Fetch HTML with requests, parse images with BeautifulSoup
# ---------------------------------------------------------------------------
def fetch_html(article_url: str, max_retries=3) -> str | None:
    """Fetch article HTML using requests (no browser needed)."""
    session = requests.Session()
    session.headers.update(SESSION_HEADERS)

    for attempt in range(max_retries):
        try:
            r = session.get(article_url, timeout=30, allow_redirects=True)
            if r.status_code == 200:
                print(f"    HTML fetched OK ({len(r.text)} chars)")
                return r.text
            print(f"    HTML fetch attempt {attempt+1}: status {r.status_code}")
            if attempt < max_retries - 1:
                time.sleep(2 + attempt * 2)
        except requests.RequestException as e:
            print(f"    HTML fetch attempt {attempt+1} error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 + attempt * 2)

    return None


def extract_images_from_html(html: str, base_url: str) -> list[dict]:
    """Parse br.de HTML for article images and captions using BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")
    images = []

    # Strategy 1: br.de <section class="ArticleModuleImage_wrapper__...">
    for section in soup.select("section[class*='ArticleModule']"):
        # Skip if inside header or footer
        if section.find_parent("footer") or section.find_parent("header"):
            continue
        # Skip video sections (ArticleModuleMedia with video player)
        classes = " ".join(section.get("class", []))
        if "ArticleModuleMedia" in classes:
            continue
        if section.find("video") or section.find("div", class_=re.compile(r"ardplayer|MediaPlayer")):
            continue
        cap = ""
        copyright_text = ""

        # Caption from MetadataBox
        cap_el = section.select_one("[class*='caption']")
        if cap_el:
            cap = clean(cap_el.get_text())

        # Copyright from MetadataBox
        copy_el = section.select_one("[class*='copyright']")
        if copy_el:
            copyright_text = clean(copy_el.get_text())

        # Fallback: JSON-LD
        if not cap:
            script_el = section.select_one('script[type="application/ld+json"]')
            if script_el and script_el.string:
                try:
                    ld = json.loads(script_el.string)
                    cap = clean(ld.get("caption", ""))
                    if not copyright_text and ld.get("copyrightHolder", {}).get("name"):
                        copyright_text = clean(ld["copyrightHolder"]["name"])
                except (json.JSONDecodeError, AttributeError):
                    pass

        # Fallback: data-sub-html
        if not cap:
            a_el = section.select_one("a[data-sub-html]")
            if a_el:
                sub_html = a_el.get("data-sub-html", "")
                sub_soup = BeautifulSoup(sub_html, "html.parser")
                cap = clean(sub_soup.get_text())

        full_cap = f"{cap} © {copyright_text}" if copyright_text else cap

        # Extract images from <figure> inside section
        for fig in section.select("figure"):
            for img in fig.select("img"):
                srcset = img.get("srcset") or img.get("data-srcset") or ""
                if srcset:
                    for item in parse_srcset(srcset, base_url):
                        if ok_ext(item["url"]):
                            images.append({**item, "cap": full_cap})

                for attr in ["src", "data-src"]:
                    u = img.get(attr)
                    if u:
                        u = urljoin(base_url, u)
                        if ok_ext(u):
                            images.append({"url": u, "w": infer_width_from_url(u), "cap": full_cap})
                            break

        # Also check <a href> for full-res link
        for a in section.select("a[href]"):
            href = urljoin(base_url, a.get("href", ""))
            if ok_ext(href):
                images.append({"url": href, "w": infer_width_from_url(href), "cap": full_cap})

    # Strategy 2: generic <figure> fallback
    for fig in soup.select("figure"):
        # Skip if already inside an ArticleModule section
        if fig.find_parent("section", class_=re.compile(r"ArticleModule")):
            continue
        # Skip if inside header or footer
        if fig.find_parent("footer") or fig.find_parent("header"):
            continue
        # Skip video thumbnails
        if fig.find("video") or fig.find_parent(class_=re.compile(r"MediaPlayer|ardplayer")):
            continue

        cap = ""
        figcap = fig.select_one("figcaption")
        if figcap:
            cap = clean(figcap.get_text())

        for img in fig.select("img"):
            srcset = img.get("srcset") or img.get("data-srcset") or ""
            if srcset:
                for item in parse_srcset(srcset, base_url):
                    if ok_ext(item["url"]):
                        images.append({**item, "cap": cap})

            for attr in ["src", "data-src"]:
                u = img.get(attr)
                if u:
                    u = urljoin(base_url, u)
                    if ok_ext(u):
                        images.append({"url": u, "w": infer_width_from_url(u), "cap": cap})
                        break

    return images


def find_embed_urls_from_html(html: str) -> list[dict]:
    """Find social media embed iframe URLs from the HTML source."""
    soup = BeautifulSoup(html, "html.parser")
    embeds = []
    seen = set()

    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src", "")

        if "instagram.com" in src:
            m = re.search(r"instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", src)
            permalink = f"https://www.instagram.com/p/{m.group(1)}/" if m else src
            if permalink not in seen:
                seen.add(permalink)
                embeds.append({"platform": "instagram", "url": permalink, "iframe_src": src})

        elif "twitter.com" in src or "platform.x.com" in src:
            m = re.search(r"[?&]id=(\d+)", src)
            tweet_url = f"https://twitter.com/i/status/{m.group(1)}" if m else src
            if tweet_url not in seen:
                seen.add(tweet_url)
                embeds.append({"platform": "twitter", "url": tweet_url, "iframe_src": src})

        elif "facebook.com" in src:
            if src not in seen:
                seen.add(src)
                embeds.append({"platform": "facebook", "url": src, "iframe_src": src})

        elif "tiktok.com" in src:
            if src not in seen:
                seen.add(src)
                embeds.append({"platform": "tiktok", "url": src, "iframe_src": src})

        elif "youtube.com/embed" in src:
            m = re.search(r"/embed/([a-zA-Z0-9_-]+)", src)
            if m:
                vid_url = f"https://www.youtube.com/watch?v={m.group(1)}"
                if vid_url not in seen:
                    seen.add(vid_url)
                    embeds.append({"platform": "youtube", "url": vid_url, "iframe_src": src})

    return embeds


# ---------------------------------------------------------------------------
# Phase 2: Screenshot social-media embeds with Playwright
# (Only used when embeds are found in the HTML)
# ---------------------------------------------------------------------------
def screenshot_embeds_with_playwright(article_url: str, embeds: list[dict],
                                       out_dir: str, prefix: str, headless=True) -> list[dict]:
    """Use Playwright to visit the page and screenshot social media embeds."""
    if not embeds or not _playwright_available:
        return []

    rows = []

    with sync_playwright() as p:
        # Use Firefox for br.de
        browser = p.firefox.launch(headless=headless)
        context = browser.new_context(
            user_agent=UA,
            locale="de-DE",
            viewport={"width": 1400, "height": 900},
            device_scale_factor=2,
        )
        page = context.new_page()

        try:
            page.goto(article_url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("load", timeout=20000)
            except Exception:
                pass

            # Dismiss consent
            for t in CONSENT_TEXTS:
                try:
                    loc = page.get_by_role("button", name=re.compile(t, re.I))
                    if loc.count() > 0:
                        loc.first.click(timeout=2000)
                        page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # Scroll to load embeds
            for _ in range(20):
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(400)
            page.evaluate("window.scrollTo(0,0)")
            page.wait_for_timeout(1000)

            # Tag and screenshot each embed
            for i, emb in enumerate(embeds):
                platform = emb["platform"]
                embed_url = emb["url"]
                iframe_src = emb.get("iframe_src", "")

                try:
                    # Find the iframe by src pattern
                    if "twitter.com" in iframe_src or "platform.x.com" in iframe_src:
                        selector = "iframe[src*='twitter.com'], iframe[src*='platform.x.com']"
                    elif "instagram.com" in iframe_src:
                        selector = "iframe[src*='instagram.com']"
                    elif "facebook.com" in iframe_src:
                        selector = "iframe[src*='facebook.com']"
                    elif "tiktok.com" in iframe_src:
                        selector = "iframe[src*='tiktok.com']"
                    elif "youtube.com" in iframe_src:
                        selector = "iframe[src*='youtube.com/embed']"
                    else:
                        continue

                    loc = page.locator(selector)
                    if loc.count() == 0:
                        continue

                    # Use the parent of the iframe for a nicer screenshot
                    target = loc.nth(i if i < loc.count() else 0)
                    target.scroll_into_view_if_needed(timeout=5000)
                    page.wait_for_timeout(2000)

                    h = hashlib.md5(embed_url.encode("utf-8")).hexdigest()[:12]
                    fname = f"{prefix}_{platform}_{h}.png"
                    fpath = os.path.join(out_dir, fname)

                    target.screenshot(path=fpath, timeout=15000)

                    if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                        caption = f"[{platform.title()} {embed_url}]"
                        rows.append({
                            "image_url": embed_url,
                            "caption": caption,
                            "path": fname,
                        })
                        print(f"    Embed screenshot OK: {fname}")
                except Exception as e:
                    print(f"    Embed screenshot failed ({platform}): {e}")
                    continue

        except Exception as e:
            print(f"    Playwright embed screenshot session failed: {e}")
        finally:
            try:
                context.close()
            finally:
                browser.close()

    return rows


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------
def _scrape_article_figures(article_url: str, out_dir="br_assets", headless=True):
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    prefix = safe_slug(article_url)

    # ---- Phase 1: Fetch HTML with requests ----
    html = fetch_html(article_url)
    if not html:
        print(f"    ERROR: Could not fetch HTML for {article_url}")
        return rows

    # ---- Phase 1b: Parse images from HTML ----
    candidates = extract_images_from_html(html, article_url)
    print(f"    Found {len(candidates)} candidate images from HTML")

    def score(c):
        w = int(c.get("w") or infer_width_from_url(c["url"]))
        return (w, ext_priority(c["url"]))

    best_by_asset = {}
    for c in candidates:
        url = c.get("url")
        if not url:
            continue
        key = canonical_key(url)
        if (key not in best_by_asset) or (score(c) > score(best_by_asset[key])):
            best_by_asset[key] = c

    groups, no_cap = {}, []
    for c in best_by_asset.values():
        capk = caption_key(c.get("cap", ""))
        (groups.setdefault(capk, []).append(c)) if capk else no_cap.append(c)

    final_images = [max(lst, key=score) for lst in groups.values()] + no_cap
    print(f"    {len(final_images)} final images after dedup")

    # ---- Phase 1c: Download images with requests ----
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Referer": article_url,
    })

    for c in final_images:
        img_url = c["url"]
        cap = c.get("cap", "")
        try:
            r = session.get(img_url, timeout=60)
            if r.status_code != 200:
                print(f"    Image download failed: {r.status_code} for {img_url[:80]}")
                continue
            ct = (r.headers.get("content-type") or "").lower()
            ext = get_ext(img_url)
            if not ext or ext not in IMG_EXT:
                if "jpeg" in ct: ext = ".jpg"
                elif "png" in ct: ext = ".png"
                elif "webp" in ct: ext = ".webp"
                elif "avif" in ct: ext = ".avif"
                else: ext = ".jpg"
            h = hashlib.md5(img_url.encode("utf-8")).hexdigest()[:12]
            fname = f"{prefix}_{h}{ext}"
            fpath = os.path.join(out_dir, fname)
            with open(fpath, "wb") as f:
                f.write(r.content)
            rows.append({
                "image_url": img_url,
                "caption": cap,
                "path": fname,
            })
            print(f"    Image OK: {fname}")
        except Exception as e:
            print(f"    Image download error: {e}")
            continue

    # ---- Phase 2: Find and screenshot social media embeds ----
    embeds = find_embed_urls_from_html(html)
    print(f"    Found {len(embeds)} social media embeds in HTML")

    if embeds:
        embed_rows = screenshot_embeds_with_playwright(
            article_url, embeds, out_dir, prefix, headless=headless
        )
        rows.extend(embed_rows)

    return rows


def handle(review_url: str, location_info: str, headless: bool = True):
    os.makedirs(location_info, exist_ok=True)
    items = _scrape_article_figures(review_url, out_dir=location_info, headless=headless)

    if items:
        df = pd.DataFrame(items)
        if not df.empty:
            csv_path = os.path.join(location_info, "image_info.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0