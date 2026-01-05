import os, re, time, hashlib
from urllib.parse import urlsplit

import pandas as pd
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
    import re as _re
    s = _re.sub(r"https?://", "", s or "")
    s = _re.sub(r"[^\w.-]+", "_", s).strip("._")
    return s[-n:] if len(s) > n else (s or "article")


JS_EXTRACT_FIGURES = """
() => {
  const ALLOWED = ['.jpg', '.jpeg', '.png', '.webp', '.avif'];
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const ok = u => !!u && ALLOWED.some(e => u.toLowerCase().split('?')[0].endsWith(e));
  const abs = (u) => { try { return new URL(u, location.href).href } catch { return null } };

  function inferWidth(u) {
    let m = u.match(/\\/(\\d{3,4})\\/[^\\/]+\\.\\w+$/)
         || u.match(/\\/fit-in\\/(\\d+)x/)
         || u.match(/[\\W_](\\d{3,4})w(?:[\\W_]|$)/)
         || u.match(/\\/(\\d{3,4})x\\d{2,4}\\//);
    return m ? parseInt(m[1], 10) : 0;
  }

  function parseSrcset(ss) {
    const out = [];
    (ss || '').split(',').map(s => s.trim()).filter(Boolean).forEach(part => {
      const bits = part.split(/\\s+/);
      const u = abs(bits[0]);
      let w = 0;
      if (bits.length > 1 && /\\d+w$/.test(bits[1])) {
        try { w = parseInt(bits[1], 10) } catch { w = 0 }
      }
      if (u) out.push({url: u, w: w || inferWidth(u)});
    });
    return out;
  }

  function extractFromImg(im, cap, source) {
    const results = [];
    const ss = im.getAttribute('srcset') || im.getAttribute('data-srcset');
    if (ss) {
      for (const it of parseSrcset(ss)) {
        if (ok(it.url)) results.push({url: it.url, w: it.w, cap, source});
      }
    }
    for (const attr of ['currentSrc', 'src', 'data-src', 'data-original']) {
      let u = attr === 'currentSrc' ? im.currentSrc : im.getAttribute(attr);
      u = u ? abs(u) : null;
      if (u && ok(u)) { results.push({url: u, w: inferWidth(u), cap, source}); break; }
    }
    return results;
  }

  /* ---------------------------------------------------------------
     Skip cover/hero images: if div[class*='default_root'] exists,
     only process <figure> elements that appear AFTER it in DOM order.
     The div is NOT a parent of the figures — it's a preceding sibling
     or element at the same level.
     --------------------------------------------------------------- */
  const bodies = Array.from(document.querySelectorAll(
    "article, main, .article, .article-body, .entry-content, .post-content, .content, " +
    "[class*='Article'], [class*='article'], body"
  ));
  const body = bodies.find(b => b && b.querySelector("figure, img")) || document.body;

  const marker = document.querySelector("div[class*='default_root']");

  const images = [];

  for (const fig of body.querySelectorAll("figure")) {
    /* If the marker exists, skip any figure that comes before it in DOM */
    if (marker && (marker.compareDocumentPosition(fig) & Node.DOCUMENT_POSITION_PRECEDING)) {
      continue;
    }
    let cap = '';
    let copyright = '';
    const rtlCapEl  = fig.querySelector("[class*='Picture_caption']");
    const rtlCopyEl = fig.querySelector("[class*='Picture_copyright']");
    const genericFC = fig.querySelector("figcaption");
    if (rtlCapEl) {
      cap = clean(rtlCapEl.textContent);
      copyright = rtlCopyEl ? clean(rtlCopyEl.textContent) : '';
    } else if (genericFC) {
      cap = clean(genericFC.textContent);
    }
    const fullCap = copyright ? (cap + ' \\u00a9 ' + copyright) : cap;

    for (const pic of fig.querySelectorAll("picture")) {
      for (const src of pic.querySelectorAll("source")) {
        const ss = src.getAttribute("srcset") || src.getAttribute("data-srcset");
        if (ss) for (const it of parseSrcset(ss)) if (ok(it.url))
          images.push({url: it.url, w: it.w, cap: fullCap, source: 'figure'});
      }
      const im = pic.querySelector("img");
      if (im) images.push(...extractFromImg(im, fullCap, 'figure'));
    }

    for (const ns of fig.querySelectorAll("noscript")) {
      const tmp = document.createElement("div");
      tmp.innerHTML = ns.innerHTML;
      const im = tmp.querySelector("img");
      if (im) images.push(...extractFromImg(im, fullCap, 'figure'));
    }

    for (const im of fig.querySelectorAll("img")) {
      if (im.closest("picture")) continue;
      images.push(...extractFromImg(im, fullCap, 'figure'));
    }

    const a = fig.querySelector("a[href]");
    if (a) {
      const u = abs(a.getAttribute("href"));
      if (ok(u)) images.push({url: u, w: inferWidth(u), cap: fullCap, source: 'figure'});
    }
  }

  return { images };
}
"""

JS_FIND_EMBEDS = """
() => {
  const embeds = [];
  let idx = 0;

  function tag(el, platform, url, caption) {
    if (el.closest('[data-sm-embed-id]')) return;
    const id = 'sm-embed-' + (idx++);
    el.setAttribute('data-sm-embed-id', id);
    embeds.push({ id, platform, url, caption });
  }

  const marker = document.querySelector("div[class*='default_root']");
  function beforeMarker(el) {
    return marker && (marker.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING);
  }

  /* On rtl.de, social embeds are rendered as iframes.
     Screenshot the iframe (or its parent wrapper) directly. */

  /* --- Instagram --- */
  for (const el of document.querySelectorAll("iframe[src*='instagram.com']")) {
    if (beforeMarker(el)) continue;
    const src = el.getAttribute('src') || '';
    const m = src.match(/instagram\\.com\\/(?:p|reel)\\/([A-Za-z0-9_-]+)/);
    const permalink = m ? 'https://www.instagram.com/p/' + m[1] + '/' : src;
    const wrapper = el.parentElement || el;
    tag(wrapper, 'instagram', permalink, '[Instagram ' + permalink + ']');
  }

  /* --- Twitter / X --- */
  for (const el of document.querySelectorAll(
    "iframe[src*='twitter.com'], iframe[src*='platform.x.com']"
  )) {
    if (beforeMarker(el)) continue;
    const src = el.getAttribute('src') || '';
    const wrapper = el.parentElement || el;
    tag(wrapper, 'twitter', src, '[Twitter ' + src + ']');
  }

  /* --- Facebook --- */
  for (const el of document.querySelectorAll("iframe[src*='facebook.com']")) {
    if (beforeMarker(el)) continue;
    const src = el.getAttribute('src') || '';
    const wrapper = el.parentElement || el;
    tag(wrapper, 'facebook', src, '[Facebook ' + src + ']');
  }

  /* --- TikTok --- */
  for (const el of document.querySelectorAll("iframe[src*='tiktok.com']")) {
    if (beforeMarker(el)) continue;
    const src = el.getAttribute('src') || '';
    const wrapper = el.parentElement || el;
    tag(wrapper, 'tiktok', src, '[TikTok ' + src + ']');
  }

  /* --- YouTube --- */
  for (const el of document.querySelectorAll(
    "iframe[src*='youtube.com/embed'], iframe[data-src*='youtube.com/embed']"
  )) {
    if (beforeMarker(el)) continue;
    const src = el.getAttribute('src') || el.getAttribute('data-src') || '';
    const m = src.match(/\\/embed\\/([a-zA-Z0-9_-]+)/);
    if (m) {
      const vidUrl = 'https://www.youtube.com/watch?v=' + m[1];
      const wrapper = el.parentElement || el;
      tag(wrapper, 'youtube', vidUrl, '[YouTube ' + vidUrl + ']');
    }
  }

  return embeds;
}
"""


def try_dismiss_consent(page) -> bool:
    for t in CONSENT_TEXTS:
        try:
            loc = page.get_by_role("button", name=re.compile(t, re.I))
            if loc.count() > 0:
                loc.first.click(timeout=1200)
                page.wait_for_timeout(300)
                return True
        except Exception:
            pass
        try:
            loc = page.locator(f"button:has-text('{t}')")
            if loc.count() > 0:
                loc.first.click(timeout=1200)
                page.wait_for_timeout(300)
                return True
        except Exception:
            pass
    for fr in page.frames:
        for t in CONSENT_TEXTS:
            try:
                loc = fr.get_by_role("button", name=re.compile(t, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(300)
                    return True
            except Exception:
                pass
            try:
                loc = fr.locator(f"button:has-text('{t}')")
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(300)
                    return True
            except Exception:
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
    import re as _re
    BLOCK_RE = _re.compile(
        r"(doubleclick\.net|googlesyndication\.com|google-analytics\.com|googletagmanager\.com"
        r"|adnxs\.com|criteo\.com|facebook\.net|connect\.facebook\.net|taboola\.com|scorecardresearch\.com"
        r"|outbrain\.com|quantserve\.com|hotjar\.com|tiktokcdn|fonts\.gstatic\.com|fonts\.googleapis\.com)",
        _re.I
    )
    def route(route):
        req = route.request
        url = req.url
        if BLOCK_RE.search(url): return route.abort()
        if req.resource_type in ("media", "font"): return route.abort()
        return route.continue_()
    page.route("**/*", route)

def robust_goto(page, url, max_tries=3, base_timeout=45000):
    from playwright.sync_api import TimeoutError as PWTimeout
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
    from playwright.sync_api import TimeoutError as PWTimeout
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
                    "() => !!document.querySelector('article, .entry-content, .post-content, "
                    "[class*=\"Article\"], body')",
                    timeout=20000
                )
            except PWTimeout:
                pass
            scroll_until_stable(page, max_scrolls=50, step=1600, idle_ms=350)
            wait_network_quiet(page, 7000)
            wait_for_images_settled(page, 12000)

            payload = page.evaluate(JS_EXTRACT_FIGURES)
            candidates = payload.get("images", [])

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
                    rows.append({
                        "image_url": img,
                        "caption": cap,
                        "path": fname,
                    })
                except Exception:
                    continue

            embed_list = page.evaluate(JS_FIND_EMBEDS)

            for emb in embed_list:
                embed_id = emb.get("id", "")
                platform = emb.get("platform", "unknown")
                embed_url = emb.get("url", "")
                caption = emb.get("caption", "")

                try:
                    loc = page.locator(f"[data-sm-embed-id='{embed_id}']")
                    if loc.count() == 0:
                        continue

                    loc.first.scroll_into_view_if_needed(timeout=5000)
                    page.wait_for_timeout(1500)

                    h = hashlib.md5(
                        (embed_id + embed_url).encode("utf-8")
                    ).hexdigest()[:12]
                    fname = f"{prefix}_{platform}_{h}.png"
                    fpath = os.path.join(out_dir, fname)

                    loc.first.screenshot(path=fpath, timeout=15000)

                    if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                        rows.append({
                            "image_url": embed_url,
                            "caption": caption,
                            "path": fname,
                        })
                except Exception:
                    continue

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