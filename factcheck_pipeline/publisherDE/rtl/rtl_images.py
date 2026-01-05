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
    "Alle akzeptieren", "Continuer sans consentir", "Continuer sans accepter", "Rejeter",
    "Tout refuser", "Refuser tout", "Continue without consent", "Continue without agreeing",
    "Reject all", "Reject All", "J'accepte", "Accepter", "OK", "I accept", "Accept all", "Agree",
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
    m = (
        re.search(r"/(\d{3,4})/[^/]+\.\w+$", u) or
        re.search(r"/fit-in/(\d+)x", u) or
        re.search(r"/(\d{3,4})x\d{2,4}/", u) or
        re.search(r"[\W_](\d{3,4})w(?:[\W_]|$)", u)
    )
    return int(m.group(1)) if m else 0

def canonical_key(u: str) -> str:
    parts = urlsplit(u)
    path = parts.path
    path = re.sub(r"/fit-in/\d+x\d+/?", "/", path, flags=re.I)
    path = re.sub(r"/\d{2,4}x\d{2,4}/", "/", path, flags=re.I)
    path = re.sub(r"(/img/\d+/\d+/[^/]+)/\d{2,4}/", r"\1/", path, flags=re.I)
    dirpath, fname = os.path.split(path)
    if fname:
        fname = re.sub(r"-(\d{2,4}x\d{2,4})(\.[a-z0-9]{2,4}$)", r"\2", fname, flags=re.I)
        fname = re.sub(r"-(scaled)(\.[a-z0-9]{2,4}$)", r"\2", fname, flags=re.I)
        path = (dirpath + "/" + fname).replace("//", "/")
    return f"{parts.netloc}{path}"

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

def extract_from_img(img_el, base_url: str, cap: str) -> list[dict]:
    results = []
    ss = img_el.get_attribute("srcset") or img_el.get_attribute("data-srcset") or ""
    if ss:
        for c in parse_srcset_py(ss, base_url):
            if ok_ext(c["url"]):
                results.append({**c, "cap": cap})
    for attr in ("currentSrc", "src", "data-src", "data-original"):
        u = img_el.evaluate("el => el.currentSrc || ''") if attr == "currentSrc" else (img_el.get_attribute(attr) or "")
        if u:
            u = urljoin(base_url, u)
            if ok_ext(u):
                results.append({"url": u, "w": infer_width_from_url(u), "cap": cap})
            break
    return results


def extract_figures(page, article_url: str) -> list[dict]:
    body_selectors = [
        "article", "main", ".article", ".article-body", ".entry-content",
        ".post-content", ".content",
    ]
    body = None
    for sel in body_selectors:
        el = page.query_selector(sel)
        if el and el.query_selector("figure, img"):
            body = el
            break
    if not body:
        body = page.query_selector("body")
    if not body:
        return []

    marker = page.query_selector("div[class*='default_root']")
    candidates = []

    for fig in body.query_selector_all("figure"):
        if marker:
            is_before = marker.evaluate(
                "(marker, fig) => !!(marker.compareDocumentPosition(fig) & Node.DOCUMENT_POSITION_PRECEDING)",
                fig
            )
            if is_before:
                continue

        rtl_cap_el = fig.query_selector("[class*='Picture_caption']")
        rtl_copy_el = fig.query_selector("[class*='Picture_copyright']")
        generic_fc = fig.query_selector("figcaption")

        if rtl_cap_el:
            cap = clean(rtl_cap_el.inner_text())
            copyright_txt = clean(rtl_copy_el.inner_text()) if rtl_copy_el else ""
        elif generic_fc:
            cap = clean(generic_fc.inner_text())
            copyright_txt = ""
        else:
            cap = ""
            copyright_txt = ""

        full_cap = f"{cap} \u00a9 {copyright_txt}" if copyright_txt else cap

        for pic in fig.query_selector_all("picture"):
            for src in pic.query_selector_all("source"):
                ss = src.get_attribute("srcset") or src.get_attribute("data-srcset") or ""
                for c in parse_srcset_py(ss, article_url):
                    if ok_ext(c["url"]):
                        candidates.append({**c, "cap": full_cap})
            img = pic.query_selector("img")
            if img:
                candidates.extend(extract_from_img(img, article_url, full_cap))

        for ns in fig.query_selector_all("noscript"):
            inner = ns.inner_html()
            if not inner:
                continue
            soup = BeautifulSoup(inner, "html.parser")
            img_tag = soup.find("img")
            if img_tag:
                u = urljoin(article_url, img_tag.get("src", ""))
                if ok_ext(u):
                    candidates.append({"url": u, "w": infer_width_from_url(u), "cap": full_cap})

        for img in fig.query_selector_all("img"):
            if img.evaluate("el => !!el.closest('picture')"):
                continue
            candidates.extend(extract_from_img(img, article_url, full_cap))

        a = fig.query_selector("a[href]")
        if a:
            href = urljoin(article_url, a.get_attribute("href") or "")
            if ok_ext(href):
                candidates.append({"url": href, "w": infer_width_from_url(href), "cap": full_cap})

    return candidates


def find_and_screenshot_embeds(page, article_url: str, out_dir: str, prefix: str) -> list[dict]:
    marker = page.query_selector("div[class*='default_root']")
    rows = []
    seen_urls = set()

    platform_selectors = [
        ("instagram", "iframe[src*='instagram.com']"),
        ("twitter", "iframe[src*='twitter.com'], iframe[src*='platform.x.com']"),
        ("facebook", "iframe[src*='facebook.com']"),
        ("tiktok", "iframe[src*='tiktok.com']"),
        ("youtube", "iframe[src*='youtube.com/embed'], iframe[data-src*='youtube.com/embed']"),
    ]

    for platform, selector in platform_selectors:
        for iframe in page.query_selector_all(selector):
            if marker:
                is_before = marker.evaluate(
                    "(marker, el) => !!(marker.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING)",
                    iframe
                )
                if is_before:
                    continue

            src = iframe.get_attribute("src") or iframe.get_attribute("data-src") or ""

            if platform == "instagram":
                m = re.search(r"instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", src)
                embed_url = f"https://www.instagram.com/p/{m.group(1)}/" if m else src
            elif platform == "youtube":
                m = re.search(r"/embed/([a-zA-Z0-9_-]+)", src)
                embed_url = f"https://www.youtube.com/watch?v={m.group(1)}" if m else src
            else:
                embed_url = src

            if embed_url in seen_urls:
                continue
            seen_urls.add(embed_url)

            try:
                wrapper = iframe.evaluate_handle("el => el.parentElement || el")
                wrapper_el = wrapper.as_element()
                target = wrapper_el if wrapper_el else iframe
                target.scroll_into_view_if_needed(timeout=5000)
                page.wait_for_timeout(1500)

                h = hashlib.md5(embed_url.encode("utf-8")).hexdigest()[:12]
                fname = f"{prefix}_{platform}_{h}.png"
                fpath = os.path.join(out_dir, fname)

                target.screenshot(path=fpath, timeout=15000)

                if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                    rows.append({
                        "image_url": embed_url,
                        "caption": f"[{platform.title()} {embed_url}]",
                        "path": fname,
                    })
            except:
                continue

    return rows


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
        try {
          Object.defineProperty(window, 'matchMedia', {
            value: (q)=>({matches: q.includes('prefers-reduced-motion'), media:q, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}}),
            configurable:true
          });
        } catch(e){}
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
        if BLOCK_RE.search(url): return route.abort()
        if req.resource_type in ("media", "font"): return route.abort()
        return route.continue_()
    page.route("**/*", route)

def robust_goto(page, url, max_tries=3, base_timeout=45000):
    for i in range(max_tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=base_timeout)
            try:
                page.wait_for_load_state("load", timeout=15000 + 5000*i)
            except PWTimeout:
                pass
            return True
        except PWTimeout:
            if i == max_tries - 1:
                return False
            time.sleep(1.0 + 0.6*i)
    return False

def scroll_until_stable(page, max_scrolls=40, step=1400, idle_ms=350):
    stable_hits = 0
    for _ in range(max_scrolls):
        h = page.evaluate("document.scrollingElement.scrollHeight")
        page.mouse.wheel(0, step)
        page.wait_for_timeout(idle_ms)
        try:
            h2 = page.evaluate("document.scrollingElement.scrollHeight")
        except PWTimeout:
            h2 = h
        stable_hits = stable_hits + 1 if h2 <= h else 0
        if stable_hits >= 3:
            break
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(300)

def wait_for_images_settled(page, timeout_ms=12000):
    script = """
      async (ms) => {
        const imgs = Array.from(document.querySelectorAll('img'));
        const decoders = imgs
          .filter(im => im.offsetParent !== null)
          .map(im => im.decode().catch(()=>{}));
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


def scrape_article_figures(article_url: str, out_dir="rtl_assets", headless=True):
    os.makedirs(out_dir, exist_ok=True)
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-dev-shm-usage",
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

            try:
                page.wait_for_function(
                    "() => !!document.querySelector('article, .entry-content, .post-content, body')",
                    timeout=20000
                )
            except PWTimeout:
                pass
            scroll_until_stable(page, max_scrolls=50, step=1600, idle_ms=350)
            wait_network_quiet(page, 7000)
            wait_for_images_settled(page, 12000)

            candidates = extract_figures(page, article_url)

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

            prefix = safe_slug(article_url)

            for c in final_images:
                img = c["url"]; cap = c.get("cap", "")
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

            rows.extend(find_and_screenshot_embeds(page, article_url, out_dir, prefix))

        finally:
            try:
                context.close()
            finally:
                browser.close()

    return rows


def handle(review_url: str, location_info: str, headless: bool = True):
    os.makedirs(location_info, exist_ok=True)
    items = scrape_article_figures(review_url, out_dir=location_info, headless=headless)

    if items:
        df = pd.DataFrame(items)
        if not df.empty:
            csv_path = os.path.join(location_info, "image_info.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
