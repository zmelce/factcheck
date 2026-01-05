
import os, re, json, hashlib, math
from urllib.parse import urlsplit
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "KHTML, like Gecko) Chrome/124.0 Safari/537.36")

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")  # we accept avif too; will save as-is
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


JS_EXTRACT = """
() => {
  const ALLOWED = ['.jpg', '.jpeg', '.png', '.webp', '.avif'];
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const ok = u => !!u && ALLOWED.some(e => u.toLowerCase().split('?')[0].endsWith(e));
  const abs = (u) => { try { return new URL(u, location.href).href } catch { return null } };

  function inferWidth(u) {
    let m = u.match(/\\/fit-in\\/(\\d+)x/);
    if (m) return parseInt(m[1], 10);
    m = u.match(/[\\W_](\\d{3,4})w(?:[\\W_]|$)/);
    if (m) return parseInt(m[1], 10);
    m = u.match(/\\/(\\d{3,4})x\\d{2,4}\\//);
    if (m) return parseInt(m[1], 10);
    return 0;
  }

  function parseSrcset(ss) {
    // returns array of {url, w}
    const out = [];
    (ss || '').split(',').map(s => s.trim()).filter(Boolean).forEach(part => {
      const bits = part.split(/\\s+/);
      const u = abs(bits[0]);
      let w = 0;
      if (bits.length > 1 && /\\d+w$/.test(bits[1])) {
        try { w = parseInt(bits[1].slice(0, -1), 10) } catch { w = 0 }
      }
      if (u) out.push({url: u, w});
    });
    return out;
  }

  const body = document.querySelector("div.c-body");
  if (!body) return {images: [], tweets: []};

  const images = [];
  // Collect from all figures inside c-body
  for (const fig of body.querySelectorAll("figure")) {
    const cap = clean((fig.querySelector("figcaption") || {}).textContent || '');
    // 1) <picture><source srcset=...>
    for (const pic of fig.querySelectorAll("picture")) {
      for (const src of pic.querySelectorAll("source")) {
        const ss = src.getAttribute("srcset") || src.getAttribute("data-srcset");
        for (const it of parseSrcset(ss)) {
          if (ok(it.url)) images.push({url: it.url, w: it.w || inferWidth(it.url), cap});
        }
      }
      // fallback <img> inside picture
      const im = pic.querySelector("img");
      if (im) {
        let u = im.currentSrc || im.getAttribute("src") || im.getAttribute("data-src") || im.getAttribute("data-original") || "";
        if (!u) {
          const ss = im.getAttribute("srcset") || im.getAttribute("data-srcset");
          if (ss) {
            for (const it of parseSrcset(ss)) {
              if (ok(it.url)) images.push({url: it.url, w: it.w || inferWidth(it.url), cap});
            }
          }
        } else {
          u = abs(u);
          if (ok(u)) images.push({url: u, w: inferWidth(u), cap});
        }
      }
    }
    // 2) <noscript> fallbacks
    for (const ns of fig.querySelectorAll("noscript")) {
      const tmp = document.createElement("div");
      tmp.innerHTML = ns.innerHTML;
      const im = tmp.querySelector("img");
      if (im) {
        const u = abs(im.getAttribute("src"));
        if (ok(u)) images.push({url: u, w: inferWidth(u), cap});
      }
    }
    // 3) Plain <img> not in <picture>
    for (const im of fig.querySelectorAll("img")) {
      if (im.closest("picture")) continue;
      let u = im.currentSrc || im.getAttribute("src") || im.getAttribute("data-src") || im.getAttribute("data-original") || "";
      if (u) {
        u = abs(u);
        if (ok(u)) images.push({url: u, w: inferWidth(u), cap});
      } else {
        const ss = im.getAttribute("srcset") || im.getAttribute("data-srcset");
        if (ss) {
          for (const it of parseSrcset(ss)) {
            if (ok(it.url)) images.push({url: it.url, w: it.w || inferWidth(it.url), cap});
          }
        }
      }
    }
  }

  // Embedded tweets (if any) inside c-body
  const tweets = [];
  for (const ifr of body.querySelectorAll(".pic-embed-container iframe[src*='platform.twitter.com/embed/Tweet.html']," +
                                          ".twitter-tweet iframe[src*='platform.twitter.com/embed/Tweet.html']")) {
    tweets.push({iframeSelector: makeUniqueSelector(ifr)});
  }

  function makeUniqueSelector(el) {
    // Build a robust selector for this iframe
    if (!el) return null;
    // prefer a stable data attribute if present
    if (el.id) return `iframe#${CSS.escape(el.id)}`;
    // fallback: nth-of-type within its parent
    const parent = el.parentElement;
    if (!parent) return "iframe";
    const tag = el.tagName.toLowerCase();
    const siblings = Array.from(parent.children).filter(n => n.tagName.toLowerCase() === tag);
    const idx = siblings.indexOf(el) + 1;
    // climb to c-body to reduce ambiguity
    let p = parent;
    let chain = [`${tag}:nth-of-type(${idx})`];
    while (p && p !== document && !p.matches("div.c-body")) {
      const t = p.tagName.toLowerCase();
      const sib = Array.from(p.parentElement ? p.parentElement.children : []).filter(n => n.tagName.toLowerCase() === t);
      const i = sib.indexOf(p) + 1;
      chain.unshift(`${t}:nth-of-type(${i})`);
      p = p.parentElement;
    }
    return `div.c-body ${chain.join(" > ")}`;
  }

  return {images, tweets};
}
"""


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
    "Continue without consent",
    "J'accepte",  # occasionally the site flips to accept to proceed
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
        except Exception:
            pass
        try:
            loc = page.locator(f"button:has-text('{t}')")
            if loc.count() > 0:
                loc.first.click(timeout=1200)
                page.wait_for_timeout(400)
                return True
        except Exception:
            pass

    for fr in page.frames:
        for t in CONSENT_TEXTS:
            try:
                loc = fr.get_by_role("button", name=re.compile(t, re.I))
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(400)
                    return True
            except Exception:
                pass
            try:
                loc = fr.locator(f"button:has-text('{t}')")
                if loc.count() > 0:
                    loc.first.click(timeout=1200)
                    page.wait_for_timeout(400)
                    return True
            except Exception:
                pass
    return False


def scrape_urls(article_url, out_dir="franceinfo_assets", headless=True, store_json=True):
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    items=[]
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

        rec = {"reviewURL": article_url}
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

            payload = page.evaluate(JS_EXTRACT)
            candidates = payload.get("images", [])
            tweet_iframes = payload.get("tweets", [])

            def score(c):
                w = int(c.get("w") or infer_width_from_url(c["url"]))
                return (w, ext_priority(c["url"]))

            best_by_asset = {}
            for c in candidates:
                key = canonical_key(c["url"])
                cur = best_by_asset.get(key)
                if not cur or score(c) > score(cur):
                    best_by_asset[key] = c

            asset_best_list = list(best_by_asset.values())

            groups = {}
            no_cap = []
            for c in asset_best_list:
                capk = caption_key(c.get("cap", ""))
                if capk:
                    groups.setdefault(capk, []).append(c)
                else:
                    no_cap.append(c)
            chosen_by_caption = [max(lst, key=score) for lst in groups.values()]
            final_images = chosen_by_caption + no_cap

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
                except Exception:
                    saved = None

                image_name = saved
                if (saved != None):
                    image_name = os.path.basename(saved)
                items.append({"image_url": img, "caption": cap, "path": image_name})

            for idx, tw in enumerate(tweet_iframes, 1):
                sel = tw.get("iframeSelector")
                if not sel:
                    continue
                loc = page.locator(sel).first
                try:
                    if loc.count() == 0:
                        continue
                    loc.wait_for(state="visible", timeout=4000)
                    loc.scroll_into_view_if_needed(timeout=2000)
                    page.wait_for_timeout(500)

                    h = hashlib.md5(f"{article_url}#tweet#{idx}".encode("utf-8")).hexdigest()[:12]
                    tpath = os.path.join(out_dir, f"{idx}_{h}.png")
                    eh = loc.element_handle()
                    if eh:
                        eh.screenshot(path=tpath)
                        image_name = os.path.basename(tpath)
                        items.append({"image_url": article_url, "caption": "", "path": image_name})
                except Exception:
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

