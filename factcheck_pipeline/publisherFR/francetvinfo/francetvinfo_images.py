import os, re, json, hashlib, math
from urllib.parse import urlsplit, urljoin
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "KHTML, like Gecko) Chrome/124.0 Safari/537.36")

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")
BODY_SEL = "div.c-body"


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

def ok_ext(u: str) -> bool:
    if not u:
        return False
    return any(u.lower().split("?")[0].endswith(e) for e in IMG_EXT)

def infer_width_from_url(u: str) -> int:
    m = re.search(r"/fit-in/(\d+)x", u)
    if m: return int(m.group(1))
    m = re.search(r"[\W_](\d{3,4})w(?:[\W_]|$)", u)
    if m: return int(m.group(1))
    m = re.search(r"/(\d{3,4})x\d{2,4}/", u)
    if m: return int(m.group(1))
    return 0

def canonical_key(u: str) -> str:
    parts = urlsplit(u)
    path = parts.path
    if "/fit-in/" in path:
        path = path.split("/fit-in/")[0]
    return f"{parts.netloc}{path}"

def caption_key(s: str) -> str:
    return clean(s).lower()

def safe_slug(s: str, n=64) -> str:
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w.-]+", "_", s).strip("._")
    return s[-n:] if len(s) > n else (s or "article")

def parse_srcset_py(srcset: str, base_url: str) -> list[dict]:
    results = []
    for part in (srcset or "").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        if not bits:
            continue
        u = urljoin(base_url, bits[0])
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                pass
        if not w:
            w = infer_width_from_url(u)
        results.append({"url": u, "w": w})
    return results


def extract_page_content(page, article_url: str):
    body = page.query_selector("div.c-body")
    if not body:
        return [], []

    images = []

    for fig in body.query_selector_all("figure"):
        cap_el = fig.query_selector("figcaption")
        cap = clean(cap_el.inner_text() if cap_el else "")

        for pic in fig.query_selector_all("picture"):
            for src in pic.query_selector_all("source"):
                ss = src.get_attribute("srcset") or src.get_attribute("data-srcset") or ""
                for c in parse_srcset_py(ss, article_url):
                    if ok_ext(c["url"]):
                        images.append({"url": c["url"], "w": c["w"], "cap": cap})
            img = pic.query_selector("img")
            if img:
                u = img.evaluate("el => el.currentSrc || ''") or img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("data-original") or ""
                if u:
                    u = urljoin(article_url, u)
                    if ok_ext(u):
                        images.append({"url": u, "w": infer_width_from_url(u), "cap": cap})
                else:
                    ss = img.get_attribute("srcset") or img.get_attribute("data-srcset") or ""
                    for c in parse_srcset_py(ss, article_url):
                        if ok_ext(c["url"]):
                            images.append({"url": c["url"], "w": c["w"], "cap": cap})

        for ns in fig.query_selector_all("noscript"):
            inner = ns.inner_html()
            if not inner:
                continue
            soup = BeautifulSoup(inner, "html.parser")
            img_tag = soup.find("img")
            if img_tag:
                u = urljoin(article_url, img_tag.get("src", ""))
                if ok_ext(u):
                    images.append({"url": u, "w": infer_width_from_url(u), "cap": cap})

        for img in fig.query_selector_all("img"):
            if img.evaluate("el => !!el.closest('picture')"):
                continue
            u = img.evaluate("el => el.currentSrc || ''") or img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("data-original") or ""
            if u:
                u = urljoin(article_url, u)
                if ok_ext(u):
                    images.append({"url": u, "w": infer_width_from_url(u), "cap": cap})
            else:
                ss = img.get_attribute("srcset") or img.get_attribute("data-srcset") or ""
                for c in parse_srcset_py(ss, article_url):
                    if ok_ext(c["url"]):
                        images.append({"url": c["url"], "w": c["w"], "cap": cap})

    tw_sel = (
        ".pic-embed-container iframe[src*='platform.twitter.com/embed/Tweet.html'],"
        ".twitter-tweet iframe[src*='platform.twitter.com/embed/Tweet.html']"
    )
    tweet_handles = body.query_selector_all(tw_sel)

    return images, tweet_handles


CONSENT_TEXTS = [
    "Continuer sans consentir",
    "Continuer sans accepter",
    "Rejeter",
    "Tout refuser",
    "Refuser tout",
    "Continue without consent",
    "Continue without agreeing",
    "Reject all",
    "Reject All",
    "J'accepte",
    "Accepter",
]

def try_dismiss_consent(page):
    for t in CONSENT_TEXTS:
        try:
            loc = page.get_by_role("button", name=re.compile(t, re.I))
            if loc.count() > 0:
                loc.first.click(timeout=1200)
                page.wait_for_timeout(400)
                return True
        except:
            pass
        try:
            loc = page.locator(f"button:has-text('{t}')")
            if loc.count() > 0:
                loc.first.click(timeout=1200)
                page.wait_for_timeout(400)
                return True
        except:
            pass

    for fr in page.frames:
        for t in CONSENT_TEXTS:
            try:
                loc = fr.get_by_role("button", name=re.compile(t, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(400)
                    return True
            except:
                pass
            try:
                loc = fr.locator(f"button:has-text('{t}')")
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(400)
                    return True
            except:
                pass
    return False


def scrape_urls(article_url, out_dir="franceinfo_assets", headless=True, store_json=True):
    os.makedirs(out_dir, exist_ok=True)
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="fr-FR",
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()

        try:
            page.goto(article_url, wait_until="domcontentloaded", timeout=60000)

            try_dismiss_consent(page)

            for _ in range(8):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(300)

            try:
                page.locator(BODY_SEL).first.wait_for(state="visible", timeout=5000)
            except PWTimeout:
                pass

            candidates, tweet_handles = extract_page_content(page, article_url)

            def score(c):
                w = int(c.get("w") or infer_width_from_url(c["url"]))
                return (w, ext_priority(c["url"]))

            best_by_asset = {}
            for c in candidates:
                key = canonical_key(c["url"])
                cur = best_by_asset.get(key)
                if not cur or score(c) > score(cur):
                    best_by_asset[key] = c

            groups = {}
            no_cap = []
            for c in best_by_asset.values():
                capk = caption_key(c.get("cap", ""))
                if capk:
                    groups.setdefault(capk, []).append(c)
                else:
                    no_cap.append(c)
            final_images = [max(lst, key=score) for lst in groups.values()] + no_cap

            prefix = safe_slug(article_url)
            for c in final_images:
                img = c["url"]
                cap = c.get("cap", "")
                saved = None
                try:
                    r = context.request.get(
                        img,
                        headers={
                            "Referer": article_url,
                            "User-Agent": UA,
                            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                        },
                        timeout=60000,
                    )
                    if r.ok:
                        ct = (r.headers.get("content-type") or "").lower()
                        ext = get_ext(img)
                        if not ext or ext not in IMG_EXT:
                            if "jpeg" in ct: ext = ".jpg"
                            elif "png" in ct: ext = ".png"
                            elif "webp" in ct: ext = ".webp"
                            elif "avif" in ct: ext = ".avif"
                            else: ext = ".jpg"
                        h = hashlib.md5(img.encode("utf-8")).hexdigest()[:12]
                        path = os.path.join(out_dir, f"{prefix}_{h}{ext}")
                        with open(path, "wb") as f:
                            f.write(r.body())
                        saved = path
                except:
                    saved = None

                image_name = os.path.basename(saved) if saved else None
                items.append({"image_url": img, "caption": cap, "path": image_name})

            for idx, ifr_handle in enumerate(tweet_handles, 1):
                try:
                    ifr_handle.scroll_into_view_if_needed(timeout=2000)
                    page.wait_for_timeout(500)
                    h = hashlib.md5(f"{article_url}#tweet#{idx}".encode("utf-8")).hexdigest()[:12]
                    tpath = os.path.join(out_dir, f"{idx}_{h}.png")
                    ifr_handle.screenshot(path=tpath)
                    image_name = os.path.basename(tpath)
                    items.append({"image_url": article_url, "caption": "", "path": image_name})
                except:
                    pass

        except Exception as e:
            print(f"[fetch error] {article_url}: {e}")

        browser.close()

    return items


def handle(review_url: str, location_info: str):
    out_list = scrape_urls(
        article_url=review_url,
        out_dir=location_info,
    )
    out_df = pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
