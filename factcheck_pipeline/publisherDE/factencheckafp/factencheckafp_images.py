import os, re, time, hashlib
from urllib.parse import urlsplit, urljoin

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")

CONSENT_TEXTS = [
    "Continuer sans consentir", "Continuer sans accepter", "Rejeter", "Tout refuser", "Refuser tout",
    "J'accepte", "Accepter", "Tout accepter", "OK",
    "Alle zulassen", "Alle akzeptieren", "Zustimmen", "Akzeptieren", "Ablehnen", "Alle ablehnen",
    "Ohne Zustimmung fortfahren", "Weiter ohne Einwilligung",
    "Continue without consent", "Continue without agreeing", "Reject all", "Reject All", "I accept", "Agree", "Accept all", "Accept All",
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

def ok_ext(u: str) -> bool:
    if not u:
        return False
    return any(u.lower().split("?")[0].endswith(e) for e in IMG_EXT)

def infer_width_from_url(u: str) -> int:
    m = (re.search(r"/fit-in/(\d+)x", u)
         or re.search(r"[\W_](\d{3,4})w(?:[\W_]|$)", u)
         or re.search(r"/(\d{3,4})x\d{2,4}/", u))
    return int(m.group(1)) if m else 0

def canonical_key(u: str) -> str:
    parts = urlsplit(u)
    path = parts.path
    if "/styles/" in path and "/public/" in path:
        try:
            path = path.split("/public/", 1)[1]
            path = "/" + path if not path.startswith("/") else path
        except:
            pass
    path = re.sub(r"(/[^/]+?)-\d{2,4}x\d{2,4}(\.[a-z0-9]{2,4})$", r"\1\2", path, flags=re.I)
    return f"{parts.netloc}{path}".lower()

def caption_key(s: str) -> str:
    return clean(s).lower()

def safe_slug(s: str, n=64) -> str:
    s = re.sub(r"https?://", "", s or "")
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


def extract_wrapper_images(page, article_url: str) -> list[dict]:
    items = []
    nodes = page.query_selector_all(".wrapper-image")

    for node in nodes:
        cap_el = node.query_selector(".legend, figcaption")
        caption = clean(cap_el.inner_text() if cap_el else "")

        seen = set()
        candidates = []

        for img in node.query_selector_all("img"):
            current_src = img.evaluate("el => el.currentSrc || ''")
            u = current_src or img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("data-original") or ""
            if u:
                u = urljoin(article_url, u)
                if ok_ext(u) and u not in seen:
                    seen.add(u)
                    w = infer_width_from_url(u) or int(img.get_attribute("width") or 0)
                    candidates.append({"url": u, "w": w})

            srcset = img.get_attribute("srcset") or img.get_attribute("data-srcset") or ""
            for c in parse_srcset_py(srcset, article_url):
                if ok_ext(c["url"]) and c["url"] not in seen:
                    seen.add(c["url"])
                    candidates.append(c)

        for ns in node.query_selector_all("noscript"):
            inner = ns.inner_html()
            if not inner:
                continue
            soup = BeautifulSoup(inner, "html.parser")
            img_tag = soup.find("img")
            if not img_tag:
                continue
            srcset = img_tag.get("srcset", "")
            for c in parse_srcset_py(srcset, article_url):
                if ok_ext(c["url"]) and c["url"] not in seen:
                    seen.add(c["url"])
                    candidates.append(c)
            u = urljoin(article_url, img_tag.get("src", ""))
            if ok_ext(u) and u not in seen:
                seen.add(u)
                w = int(img_tag.get("width") or 0) or infer_width_from_url(u)
                candidates.append({"url": u, "w": w})

        if candidates:
            items.append({"caption": caption, "candidates": candidates})

    return items


def try_dismiss_consent(page) -> bool:
    for t in CONSENT_TEXTS:
        try:
            loc = page.get_by_role("button", name=re.compile(t, re.I))
            if loc.count() > 0:
                loc.first.click(timeout=1200)
                page.wait_for_timeout(300)
                return True
        except:
            pass
        try:
            loc = page.locator(f"button:has-text('{t}')")
            if loc.count() > 0:
                loc.first.click(timeout=1200)
                page.wait_for_timeout(300)
                return True
        except:
            pass
    for fr in page.frames:
        for t in CONSENT_TEXTS:
            try:
                loc = fr.get_by_role("button", name=re.compile(t, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(300)
                    return True
            except:
                pass
            try:
                loc = fr.locator(f"button:has-text('{t}')")
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(300)
                    return True
            except:
                pass
    return False


def add_lazyload_forcers(context):
    context.add_init_script("""
      (() => {
        try {
          const IO = window.IntersectionObserver;
          window.IntersectionObserver = class {
            constructor(cb, opts){ this._cb = cb; this._opts = opts; }
            observe(el){ this._cb([{isIntersecting:true, intersectionRatio:1, target:el}], this); }
            unobserve(){ }
            disconnect(){ }
            takeRecords(){ return []; }
          };
        } catch (e) {}
        try { Object.defineProperty(navigator, 'connection', {value:{saveData:true}, configurable:true}); } catch(e){}
        try { Object.defineProperty(window, 'matchMedia', { value: (q)=>({matches: q.includes('prefers-reduced-motion'), media:q, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}}), configurable:true }); } catch(e){}
      })();
    """)

def route_block_noise(page):
    BLOCK_RE = re.compile(
        r"(doubleclick\.net|googlesyndication\.com|google-analytics\.com|googletagmanager\.com"
        r"|adnxs\.com|criteo\.com|facebook\.net|connect\.facebook\.net|taboola\.com|scorecardresearch\.com"
        r"|outbrain\.com|quantserve\.com|hotjar\.com|tiktokcdn|fonts\.gstatic\.com|fonts\.googleapis\.com)",
        re.I
    )
    def route(route):
        req = route.request
        url = req.url
        if BLOCK_RE.search(url):
            return route.abort()
        if req.resource_type in ("media", "font"):
            return route.abort()
        return route.continue_()
    page.route("**/*", route)

def robust_goto(page, url, max_tries=3, base_timeout=45000):
    for i in range(max_tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=base_timeout)
            try: page.wait_for_load_state("load", timeout=15000 + 5000*i)
            except PWTimeout: pass
            return True
        except PWTimeout:
            if i == max_tries - 1: return False
            time.sleep(1.2 * (i+1))
    return False

def scroll_until_stable(page, max_scrolls=40, step=1400, idle_ms=350):
    stable_hits = 0
    for _ in range(max_scrolls):
        h = page.evaluate("document.scrollingElement.scrollHeight")
        page.mouse.wheel(0, step)
        page.wait_for_timeout(idle_ms)
        h2 = page.evaluate("document.scrollingElement.scrollHeight")
        stable_hits = stable_hits + 1 if h2 <= h else 0
        if stable_hits >= 3: break
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(300)

def wait_for_images_settled(page, timeout_ms=12000):
    script = """
      async (ms) => {
        const imgs = Array.from(document.querySelectorAll('img'));
        const decoders = imgs.filter(im => im.offsetParent !== null).map(im => im.decode().catch(()=>{}));
        await Promise.race([ Promise.allSettled(decoders), new Promise(res => setTimeout(res, ms)) ]);
        return true;
      }
    """
    try:
        page.evaluate(script, timeout_ms)
    except PWTimeout:
        pass

def wait_network_quiet(page, timeout_ms=6000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PWTimeout:
        pass


def scrape_article_wrapper_images(article_url: str, out_dir="wrapper_assets", headless=True):
    os.makedirs(out_dir, exist_ok=True)
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox","--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-renderer-backgrounding",
                "--disable-features=PreloadMediaEngagementData,AutofillServerCommunication",
            ],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="de-DE",
            viewport={"width": 1400, "height": 900},
            device_scale_factor=2,
        )
        add_lazyload_forcers(context)
        page = context.new_page()
        route_block_noise(page)

        try:
            ok = robust_goto(page, article_url, max_tries=3, base_timeout=60000)
            if not ok:
                raise PWTimeout(f"Navigation failed for {article_url}")

            try_dismiss_consent(page)
            try: page.wait_for_function("() => !!document.querySelector('.wrapper-image, article, body')", timeout=20000)
            except PWTimeout: pass

            scroll_until_stable(page, max_scrolls=50, step=1600, idle_ms=350)
            wait_network_quiet(page, 7000)
            wait_for_images_settled(page, 12000)

            items = extract_wrapper_images(page, article_url)

            def score(c):
                w = int(c.get("w") or 0) or infer_width_from_url(c.get("url", ""))
                return (w, ext_priority(c.get("url", "")))

            best_by_asset = {}
            for it in items:
                for c in it.get("candidates", []):
                    u = c.get("url")
                    if not u: continue
                    k = canonical_key(u)
                    if (k not in best_by_asset) or (score(c) > score(best_by_asset[k])):
                        best_by_asset[k] = c | {"caption": it.get("caption", "")}

            groups = {}
            for k, c in best_by_asset.items():
                capk = caption_key(c.get("caption", ""))
                groups.setdefault(capk, []).append(c)

            final_images = []
            for capk, lst in groups.items():
                final_images.append(max(lst, key=score))

            prefix = safe_slug(article_url)

            for c in final_images:
                img = c.get("url"); cap = c.get("caption", "")
                if not img: continue
                try:
                    r = context.request.get(
                        img,
                        headers={
                            "Referer": article_url, "User-Agent": UA,
                            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                        },
                        timeout=90000,
                    )
                    if not r.ok:
                        continue
                    ct = (r.headers.get("content-type") or "").lower()
                    ext = get_ext(img)
                    if not ext or ext not in IMG_EXT:
                        if "jpeg" in ct: ext = ".jpg"
                        elif "png" in ct: ext = ".png"
                        elif "webp" in ct: ext = ".webp"
                        elif "avif" in ct: ext = ".avif"
                        else: ext = ".jpg"
                    h = hashlib.md5(img.encode("utf-8")).hexdigest()[:12]
                    fname = f"{prefix}_{h}{ext}"
                    fpath = os.path.join(out_dir, fname)
                    with open(fpath, "wb") as f:
                        f.write(r.body())
                    rows.append({"image_url": img, "caption": cap, "path": fname})
                except:
                    continue

        finally:
            context.close()
            browser.close()

    return rows


def handle(review_url: str, location_info="wrapper_assets", headless=False):
    os.makedirs(location_info, exist_ok=True)
    items = scrape_article_wrapper_images(review_url, out_dir=location_info, headless=headless)

    if items:
        df = pd.DataFrame(items)
        if not df.empty:
            csv_path = os.path.join(location_info, "image_info.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8")
            return items

    return items
