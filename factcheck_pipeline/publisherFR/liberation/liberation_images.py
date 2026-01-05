import os, re, json, time, hashlib
from urllib.parse import urlsplit, urlparse, parse_qs

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")

CONSENT_TEXTS = [
    "Continuer sans consentir","Continuer sans accepter","Rejeter","Tout refuser","Refuser tout",
    "Continue without consent","Continue without agreeing","Reject all","Reject All","J'accepte","Accepter",
]


def clean(s: str | None) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", (s or "")).strip()

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
    import re as _re
    m = _re.search(r"/fit-in/(\d+)x", u) or _re.search(r"[\W_](\d{3,4})w(?:[\W_]|$)", u) or _re.search(r"/(\d{3,4})x\d{2,4}/", u)
    return int(m.group(1)) if m else 0

def canonical_key(u: str) -> str:
    parts = urlsplit(u)
    path = parts.path
    if "/fit-in/" in path:
        path = path.split("/fit-in/")[0]
    return f"{parts.netloc}{path}"

def caption_key(s: str) -> str:
    return clean(s).lower()

def safe_slug(s: str, n=64) -> str:
    import re as _re
    s = _re.sub(r"https?://", "", s)
    s = _re.sub(r"[^\w.-]+", "_", s).strip("._")
    return s[-n:] if len(s) > n else (s or "article")


JS_EXTRACT_LIBE = """
( ) => {
  const ALLOWED = ['.jpg', '.jpeg', '.png', '.webp', '.avif'];
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const ok = u => !!u && ALLOWED.some(e => u.toLowerCase().split('?')[0].endsWith(e));
  const abs = (u) => { try { return new URL(u, location.href).href } catch { return null } };

  function inferWidth(u) {
    let m = u.match(/\\/fit-in\\/(\\d+)x/) || u.match(/[\\W_](\\d{3,4})w(?:[\\W_]|$)/) || u.match(/\\/(\\d{3,4})x\\d{2,4}\\//);
    return m ? parseInt(m[1], 10) : 0;
  }
  function parseSrcset(ss) {
    const out = [];
    (ss || '').split(',').map(s => s.trim()).filter(Boolean).forEach(part => {
      const bits = part.split(/\\s+/);
      const u = abs(bits[0]);
      let w = 0;
      if (bits.length > 1 && /\\d+w$/.test(bits[1])) { try { w = parseInt(bits[1], 10) } catch { w = 0 } }
      if (u) out.push({url: u, w});
    });
    return out;
  }

  const bodies = Array.from(document.querySelectorAll(
    "article .article-body, article .article__content, article .paywall-content, article"
  ));
  const body = bodies.find(b => b && b.querySelector("figure, img, iframe")) || document.body;
  if (!body) return {images: [], tweets: []};

  const images = [];
  for (const fig of body.querySelectorAll("div.ImageArticle__Container-sc-1bs3lof-0 figure.article-body-image-element, figure.article-body-image-element, figure")) {
    const cap = clean((fig.querySelector("figcaption") || {}).textContent || '');

    for (const pic of fig.querySelectorAll("picture")) {
      for (const src of pic.querySelectorAll("source")) {
        const ss = src.getAttribute("srcset") || src.getAttribute("data-srcset");
        if (ss) for (const it of parseSrcset(ss)) if (ok(it.url)) images.push({url: it.url, w: it.w || inferWidth(it.url), cap});
      }
      const im = pic.querySelector("img");
      if (im) {
        let u = im.currentSrc || im.getAttribute("src") || im.getAttribute("data-src") || im.getAttribute("data-original") || "";
        if (!u) {
          const ss = im.getAttribute("srcset") || im.getAttribute("data-srcset");
          if (ss) for (const it of parseSrcset(ss)) if (ok(it.url)) images.push({url: it.url, w: it.w || inferWidth(it.url), cap});
        } else { u = abs(u); if (ok(u)) images.push({url: u, w: inferWidth(u), cap}); }
      }
    }

    for (const ns of fig.querySelectorAll("noscript")) {
      const tmp = document.createElement("div");
      tmp.innerHTML = ns.innerHTML;
      const im = tmp.querySelector("img");
      if (im) { const u = abs(im.getAttribute("src")); if (ok(u)) images.push({url: u, w: inferWidth(u), cap}); }
    }

    for (const im of fig.querySelectorAll("img")) {
      if (im.closest("picture")) continue;
      let u = im.currentSrc || im.getAttribute("src") || im.getAttribute("data-src") || im.getAttribute("data-original") || "";
      if (u) { u = abs(u); if (ok(u)) images.push({url: u, w: inferWidth(u), cap}); }
      else {
        const ss = im.getAttribute("srcset") || im.getAttribute("data-srcset");
        if (ss) for (const it of parseSrcset(ss)) if (ok(it.url)) images.push({url: it.url, w: it.w || inferWidth(it.url), cap});
      }
    }
  }

  const tweets = [];
  const sel = ".twitter-tweet iframe[src*='platform.twitter.com/embed/Tweet.html'], iframe[src*='platform.twitter.com/embed/Tweet.html']";
  for (const ifr of body.querySelectorAll(sel)) {
    tweets.push({ iframeSelector: makeUniqueSelector(ifr) });
  }

  function makeUniqueSelector(el) {
    if (!el) return null;
    if (el.id) return `iframe#${CSS.escape(el.id)}`;
    let chain = [];
    let cur = el;
    while (cur && cur !== document) {
      const parent = cur.parentElement;
      const tag = cur.tagName.toLowerCase();
      if (!parent) { chain.unshift(tag); break; }
      const siblings = Array.from(parent.children).filter(n => n.tagName.toLowerCase() === tag);
      const idx = siblings.indexOf(cur) + 1;
      chain.unshift(`${tag}:nth-of-type(${idx})`);
      cur = parent;
      if (parent.matches("article")) break;
    }
    return chain.length ? chain.join(" > ") : "iframe";
  }

  return { images, tweets };
}
"""


def try_dismiss_consent(page) -> bool:
    for t in CONSENT_TEXTS:
        try:
            loc = page.get_by_role("button", name=re.compile(t, re.I))
            if loc.count() > 0: loc.first.click(timeout=1200); page.wait_for_timeout(300); return True
        except Exception: pass
        try:
            loc = page.locator(f"button:has-text('{t}')")
            if loc.count() > 0: loc.first.click(timeout=1200); page.wait_for_timeout(300); return True
        except Exception: pass
    for fr in page.frames:
        for t in CONSENT_TEXTS:
            try:
                loc = fr.get_by_role("button", name=re.compile(t, re.I))
                if loc.count() > 0: loc.first.click(timeout=1200); page.wait_for_timeout(300); return True
            except Exception: pass
            try:
                loc = fr.locator(f"button:has-text('{t}')")
                if loc.count() > 0: loc.first.click(timeout=1200); page.wait_for_timeout(300); return True
            except Exception: pass
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
    def _route(route):
        req = route.request
        url = req.url
        if BLOCK_RE.search(url):
            return route.abort()
        if req.resource_type in ("media", "font"):
            return route.abort()
        return route.continue_()
    page.route("**/*", _route)

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
        const imgs = Array.from(document.querySelectorAll('article img'));
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


def scrape_article_libe(article_url: str, out_dir="libe_assets", headless=True):
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
            locale="fr-FR",
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

            try: page.wait_for_function("() => !!document.querySelector('article')", timeout=20000)
            except PWTimeout: pass
            scroll_until_stable(page, max_scrolls=50, step=1600, idle_ms=350)
            wait_network_quiet(page, 7000)
            wait_for_images_settled(page, 12000)

            payload = page.evaluate(JS_EXTRACT_LIBE)
            candidates = payload.get("images", [])
            tweet_iframes = payload.get("tweets", [])

            def score(c):
                w = int(c.get("w") or infer_width_from_url(c["url"]))
                return (w, ext_priority(c["url"]))

            best_by_asset = {}
            for c in candidates:
                key = canonical_key(c["url"])
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
                            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
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
                    with open(fpath, "wb") as f: f.write(r.body())
                    rows.append({"image_url": img, "caption": cap, "path": fname})
                except Exception:
                    continue  # skip failed downloads silently

            for idx, tw in enumerate(tweet_iframes, 1):
                sel = tw.get("iframeSelector")
                if not sel:
                    continue
                saved = False
                for attempt in range(3):
                    try:
                        loc = page.locator(sel).first
                        if loc.count() == 0:
                            raise RuntimeError("iframe not found")
                        loc.scroll_into_view_if_needed(timeout=4000)
                        page.wait_for_timeout(600 + attempt*200)

                        eh = loc.element_handle()
                        if not eh:
                            raise RuntimeError("no element handle")

                        tag = eh.evaluate("el => el.tagName").lower()
                        if tag != "iframe":
                            raise RuntimeError("not an iframe")

                        src = eh.get_attribute("src") or ""
                        if "platform.twitter.com/embed/Tweet.html" not in src:
                            raise RuntimeError("not a tweet iframe")

                        fr = eh.content_frame()
                        if fr:
                            try: fr.wait_for_load_state("domcontentloaded", timeout=9000 + attempt*1000)
                            except PWTimeout: pass

                        bbox = loc.bounding_box()
                        if not bbox:
                            raise RuntimeError("no bounding box")

                        from urllib.parse import urlparse, parse_qs
                        tweet_id = None
                        try:
                            q = parse_qs(urlparse(src).query)
                            tweet_id = q.get("id", [None])[0]
                        except Exception:
                            pass

                        import math
                        pad = 4
                        x = max(0, (bbox["x"] or 0) - pad)
                        y = max(0, (bbox["y"] or 0) - pad)
                        w = (bbox["width"] or 0) + pad*2
                        h = (bbox["height"] or 0) + pad*2
                        if w <= 0 or h <= 0:
                            raise RuntimeError("invalid bbox")

                        name = f"tweet_{tweet_id or idx}_{hashlib.md5(f'{article_url}#tweet#{idx}'.encode('utf-8')).hexdigest()[:12]}.png"
                        tpath = os.path.join(out_dir, name)
                        page.screenshot(path=tpath, clip={"x": x, "y": y, "width": w, "height": h})
                        rows.append({"image_url": tweet_id or src, "caption": "", "path": name})
                        saved = True
                        break
                    except Exception:
                        page.mouse.wheel(0, 1200)
                        page.wait_for_timeout(500 + attempt*300)
                        continue

        finally:
            context.close()
            browser.close()

    return rows


def handle(review_url: str, location_info: str, headless: bool = True):
    os.makedirs(location_info, exist_ok=True)
    items = scrape_article_libe(review_url, out_dir=location_info, headless=headless)

    if items:
        df = pd.DataFrame(items)
        if not df.empty:
            csv_path = os.path.join(location_info, "image_info.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8")

    return 0
