
import re, time
from typing import List, Dict
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

CONSENT_TEXTS = [
    "Continuer sans consentir","Continuer sans accepter","Rejeter","Tout refuser","Refuser tout",
    "Continue without consent","Continue without agreeing","Reject all","Reject All","J'accepte","Accepter",
]


Y_EMBED = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})", re.I)
TW_IFRAME = re.compile(r"https?://platform\.twitter\.com/embed/Tweet\.html\?[^\"'>]+", re.I)
TW_ID = re.compile(r"(?:\?|&)id=(\d+)(?:&|$)")
TW_STATUS = re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/]+/status/(\d+)", re.I)

def _add_query(u: str, extra: dict) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs.update(extra or {})
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), sp.fragment))

def canon_youtube(u: str) -> str | None:
    m = Y_EMBED.search(u or "")
    if not m: return None
    vid = m.group(1)
    return _add_query(f"https://www.youtube.com/embed/{vid}", {"autoplay":"1"})

def canon_tweet_from_iframe(u: str) -> str | None:
    if not TW_IFRAME.search(u or ""): return None
    m = TW_ID.search(u)
    return f"https://x.com/i/web/status/{m.group(1)}" if m else None

def canon_tweet_from_anchor(u: str) -> str | None:
    m = TW_STATUS.search(u or "")
    return f"https://x.com/i/web/status/{m.group(1)}" if m else None

def _is_visible(locator, min_w=40, min_h=40) -> bool:
    try:
        box = locator.bounding_box()
        if not box: return False
        return box.get("width",0) >= min_w and box.get("height",0) >= min_h
    except Exception:
        return False


def add_lazyload_forcers(context):
    context.add_init_script("""
      (() => {
        try {
          const IO = window.IntersectionObserver;
          window.IntersectionObserver = class {
            constructor(cb, opts){ this._cb = cb; this._opts = opts; }
            observe(el){ this._cb([{isIntersecting:true, intersectionRatio:1, target:el}], this); }
            unobserve(){} disconnect(){} takeRecords(){return[]}
          };
        } catch(e){}
        try { Object.defineProperty(navigator,'connection',{value:{saveData:true}, configurable:true}); } catch(e){}
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
    def _route(route):
        req = route.request
        if BLOCK_RE.search(req.url) or req.resource_type in ("media","font"):
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
            if i == max_tries-1: return False
            time.sleep(1.0 + 0.6*i)
    return False

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
    page.wait_for_timeout(250)


def tweet_node_has_visible_video(page, node_locator) -> bool:
    vid = node_locator.locator("video")
    for i in range(min(vid.count(), 3)):
        if _is_visible(vid.nth(i), 20, 20): return True
    vc = node_locator.locator("[data-testid='videoComponent']")
    for i in range(min(vc.count(), 3)):
        if _is_visible(vc.nth(i), 20, 20): return True
    thumb = node_locator.locator("img[src*='ext_tw_video_thumb'], [style*='ext_tw_video_thumb']")
    for i in range(min(thumb.count(), 3)):
        if _is_visible(thumb.nth(i), 20, 20): return True
    play_btn = node_locator.get_by_role("button", name=re.compile(r"Lire|Play|Regarder|Watch", re.I))
    for i in range(min(play_btn.count(), 2)):
        if _is_visible(play_btn.nth(i), 20, 20): return True
    return False

def tweet_iframe_has_video(fr) -> bool:
    try:
        if fr.locator("video").count() > 0: return True
        if fr.locator("[data-testid='videoComponent']").count() > 0: return True
        if fr.locator("img[src*='ext_tw_video_thumb'], [style*='ext_tw_video_thumb']").count() > 0: return True
        if fr.get_by_role("button", name=re.compile(r"Play|Watch|Lire|Regarder", re.I)).count() > 0: return True
    except Exception:
        pass
    return False


def _extract_links_on_page(page) -> Dict[str, List[str]]:
    results: Dict[str, List[str]] = {"youtube": [], "twitter": []}
    seen = set()

    scope = page.locator("article, main, .article, .article-body, .page-content-wrapper, body").first

    y_iframes = scope.locator("iframe[src*='youtube.com/embed'], iframe[src*='youtube-nocookie.com/embed']")
    for i in range(y_iframes.count()):
        fr = y_iframes.nth(i)
        if not _is_visible(fr): continue
        src = (fr.get_attribute("src") or "").strip()
        cu = canon_youtube(src)
        if cu and cu not in seen:
            seen.add(cu); results["youtube"].append(cu)
        try:
            eh = fr.element_handle()
            if eh:
                srcdoc = (eh.get_attribute("srcdoc") or "").strip()
                if srcdoc:
                    for m in Y_EMBED.finditer(srcdoc):
                        cu2 = _add_query(f"https://www.youtube.com/embed/{m.group(1)}", {"autoplay":"1"})
                        if cu2 not in seen:
                            seen.add(cu2); results["youtube"].append(cu2)
        except Exception:
            pass

    tw_ifr = scope.locator("iframe[src*='platform.twitter.com/embed/Tweet.html']")
    for i in range(tw_ifr.count()):
        fr_loc = tw_ifr.nth(i)
        if not _is_visible(fr_loc, 20, 20): continue
        src = (fr_loc.get_attribute("src") or "").strip()
        fr_handle = fr_loc.element_handle()
        fr = fr_handle.content_frame() if fr_handle else None
        if not fr or not tweet_iframe_has_video(fr): continue
        cu = canon_tweet_from_iframe(src)
        if cu and cu not in seen:
            seen.add(cu); results["twitter"].append(cu)

    tweet_like = scope.locator("[data-testid='tweet'], article[role='article'], div[role='article']")
    for i in range(min(tweet_like.count(), 200)):
        tw = tweet_like.nth(i)
        if not _is_visible(tw, 20, 20): continue
        if not tweet_node_has_visible_video(page, tw): continue
        anchors = tw.locator("a[href*='/status/']")
        for j in range(min(anchors.count(), 20)):
            href = (anchors.nth(j).get_attribute("href") or "").strip()
            cu = canon_tweet_from_anchor(href)
            if cu and cu not in seen:
                seen.add(cu); results["twitter"].append(cu)
                break

    for k, v in list(results.items()):
        v = sorted(set(v))
        if v: results[k] = v
        else: results.pop(k, None)
    return results

def extract_visible_video_and_tweet_links(url: str, headless: bool = True) -> List[str]:
    """Return a flat list of visible video links (YouTube embeds + tweet videos)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox","--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-renderer-backgrounding",
            ],
        )
        context = browser.new_context(
            user_agent=UA, locale="fr-FR",
            viewport={"width": 1400, "height": 900}, device_scale_factor=2,
        )
        add_lazyload_forcers(context)
        page = context.new_page()
        route_block_noise(page)

        try:
            ok = robust_goto(page, url, max_tries=3, base_timeout=60000)
            if not ok:
                return []
            try_dismiss_consent(page)
            try: page.wait_for_function("() => !!document.querySelector('article, main, body')", timeout=20000)
            except PWTimeout: pass
            scroll_until_stable(page, max_scrolls=50, step=1600, idle_ms=350)

            data = _extract_links_on_page(page)
            out: List[str] = []
            if data.get("youtube"): out.extend(data["youtube"])
            if data.get("twitter"): out.extend(data["twitter"])
            return out

        finally:
            context.close()
            browser.close()


def handle(review_url: str) -> List[str]:
    try:
        return extract_visible_video_and_tweet_links(review_url, headless=True)
    except Exception:
        return []
