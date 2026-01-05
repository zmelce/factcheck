
import os, re, json, hashlib
from urllib.parse import urljoin, urlsplit, parse_qs
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; numerama-img-scraper/1.2)"}
IMG_EXT  = (".jpg", ".jpeg", ".png", ".webp", ".avif")

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except:
    _PLAYWRIGHT_OK = False

def absurl(base, u):
    if not u:
        return None
    u = u.strip()
    if u.startswith("data:"):
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin(base, u)

def pick_largest_from_srcset(srcset, base):
    best_u, best_w = None, -1
    for part in str(srcset).split(","):
        p = part.strip().split()
        if not p:
            continue
        u = absurl(base, p[0])
        w = 0
        if len(p) > 1:
            d = p[1].lower()
            if d.endswith("w"):
                try: w = int(d[:-1])
                except: w = 0
            elif d.endswith("x"):
                try: w = int(float(d[:-1]) * 1000)
                except: w = 0
        if u and w > best_w:
            best_u, best_w = u, w
    return best_u

def clean_text(t): return re.sub(r"\s+", " ", (t or "").strip())
def get_ext(u: str) -> str: return os.path.splitext(urlsplit(u).path.lower())[1]

def safe_slug(s, length=64):
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w.-]+", "_", s)
    s = s.strip("._")
    return s[-length:] if len(s) > length else (s or "article")

def download_image(url, dest_dir, prefix):
    if not url:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    try:
        r = requests.get(url, headers=HEADERS, timeout=25, stream=True)
        r.raise_for_status()
        ext = get_ext(url)
        if ext not in IMG_EXT:
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if "jpeg" in ct: ext = ".jpg"
            elif "png" in ct: ext = ".png"
            elif "webp" in ct: ext = ".webp"
            else: ext = ".jpg"
        h = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        fpath = os.path.join(dest_dir, f"{h}{ext}")
        with open(fpath, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk: f.write(chunk)
        return fpath
    except Exception as e:
        print(f"[download error] {url}: {e}")
        return None

_TWEET_STATUS_RE = re.compile(r"https?://(www\.)?(x|twitter)\.com/[^/]+/status/(\d+)")

def tweet_id_from_url(u: str) -> str | None:
    if not u: return None
    try:
        q = parse_qs(urlsplit(u).query)
        if "id" in q and q["id"]:
            return q["id"][0]
        m = _TWEET_STATUS_RE.search(u)
        if m:
            return m.group(3)
    except:
        pass
    return None

def build_embed_from_id(tweet_id: str, lang="fr", width_px=550) -> str:
    return f"https://platform.twitter.com/embed/Tweet.html?id={tweet_id}&theme=light&lang={lang}&dnt=true&width={width_px}px"

def capture_tweet_screenshot(any_tweet_ref: str, dest_dir: str, prefix: str, width: int = 550, timeout_ms: int = 45000):
    if not _PLAYWRIGHT_OK:
        print("[tweet] Playwright not available; skipping screenshot")
        return None

    os.makedirs(dest_dir, exist_ok=True)
    tid = tweet_id_from_url(any_tweet_ref)
    if not tid:
        tid = hashlib.md5(any_tweet_ref.encode("utf-8")).hexdigest()[:12]
    embed_url = build_embed_from_id(tid, width_px=width)

    out_path = os.path.join(dest_dir, f"{tid}.png")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": width, "height": 1000},
                device_scale_factor=1.0,
                user_agent=HEADERS["User-Agent"]
            )
            page = context.new_page()
            page.goto(embed_url, wait_until="networkidle", timeout=timeout_ms)
            for sel in ["article", "[data-testid='tweet']", "blockquote", "body"]:
                try:
                    page.wait_for_selector(sel, timeout=6000)
                    break
                except:
                    continue
            page.screenshot(path=out_path, full_page=True)
            context.close()
            browser.close()
        return out_path
    except Exception as e:
        print(f"[tweet] failed to capture screenshot: {e}")
        return None

def is_numerama_main_container(tag: Tag) -> bool:
    if not isinstance(tag, Tag) or tag.name != "div":
        return False
    classes = set(tag.get("class", []))
    if {"article-content", "post-content"}.issubset(classes):
        return True
    if {"wp-block-bsaweb-blocks-grid-item", "col-8@md"}.issubset(classes):
        return True
    return False

def find_container(soup: BeautifulSoup) -> Tag | None:
    c = soup.find(is_numerama_main_container)
    if c:
        return c
    return soup.select_one("div.article-content.post-content")

def best_img_in_figure(fig: Tag, base: str):
    for pic in fig.find_all("picture"):
        for src in pic.find_all("source"):
            ss = src.get("srcset") or src.get("data-srcset")
            if ss:
                u = pick_largest_from_srcset(ss, base)
                if u:
                    return u
        im = pic.find("img")
        if im:
            u = im.get("src") or im.get("data-src") or im.get("data-original")
            if not u:
                ss = im.get("srcset") or im.get("data-srcset")
                if ss:
                    u = pick_largest_from_srcset(ss, base)
            if u:
                u = absurl(base, u)
                if u:
                    return u
    im = fig.find("img")
    if im:
        u = im.get("src") or im.get("data-src") or im.get("data-original")
        if not u:
            ss = im.get("srcset") or im.get("data-srcset")
            if ss:
                u = pick_largest_from_srcset(ss, base)
        if u:
            u = absurl(base, u)
            if u:
                return u
    a = fig.find("a", href=True)
    if a:
        href = absurl(base, a["href"])
        if href and get_ext(href) in IMG_EXT:
            return href
    return None

def find_tweet_ref_in_figure(fig: Tag, base: str) -> str | None:

    for ifr in fig.find_all("iframe"):
        src = ifr.get("src") or ifr.get("data-src")
        src = absurl(base, src)
        if src and "platform.twitter.com" in src:
            return src

    for bq in fig.find_all("blockquote", class_=lambda c: c and "twitter-tweet" in (c if isinstance(c, list) else [c])):
        links = bq.find_all("a", href=True)
        for a in reversed(links):
            href = absurl(base, a["href"])
            if href and _TWEET_STATUS_RE.search(href):
                return href

    any_id_el = fig.find(attrs={"data-tweet-id": True})
    if any_id_el:
        tid = str(any_id_el.get("data-tweet-id")).strip()
        if tid.isdigit():
            return build_embed_from_id(tid)

    return None

def extract_assets_numerama(article_url):
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"[fetch error] {article_url}: {e}")
        return []

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except:
        soup = BeautifulSoup(r.text, "html.parser")

    base = r.url
    container = find_container(soup) or soup

    out, seen = [], set()

    for fig in container.find_all("figure"):
        cap = ""
        fc = fig.find("figcaption")
        if fc:
            cap = clean_text(fc.get_text(" "))

        tref = find_tweet_ref_in_figure(fig, base)
        if tref:
            tid = tweet_id_from_url(tref) or ""
            key = f"tweet:{tid or tref}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": "tweet", "tweet_ref": tref, "tweet_id": tid, "caption": cap})
            continue

        img_url = best_img_in_figure(fig, base)
        if img_url:
            ext = get_ext(img_url)
            if ext and ext not in IMG_EXT:
                continue
            if img_url in seen:
                continue
            seen.add(img_url)
            out.append({"kind": "image", "image_url": img_url, "caption": cap})

    return out

def enrich_dataframe_with_images_list(article_url="reviewURL",
                                      out_dir="numerama_assets",
                                      download=True,
                                      store_json=True,
                                      screenshot_tweets=True):

    items_out = []
    img_count = 0
    if article_url:
        assets = extract_assets_numerama(article_url)
        if download and assets:
            prefix = safe_slug(article_url)
            for it in assets:
                if it["kind"] == "image":
                    path = download_image(it["image_url"], out_dir, prefix)
                    image_name = os.path.basename(path)
                    items_out.append({
                        "image_url": it["image_url"],
                        "caption": it.get("caption", ""),
                        "path": image_name
                    })

                else:
                    if screenshot_tweets:
                        png = capture_tweet_screenshot(it.get("tweet_ref",""), out_dir, f"{prefix}")
                        tweet_name = os.path.basename(png)
                    else:
                        tweet_name = None
                    items_out.append({
                        "image_url": it.get("tweet_ref",""),
                        "caption": it.get("caption",""),
                        "path": tweet_name
                    })
        else:
            items_out = assets

    return items_out

def handle(review_url: str, location_info: str):
    out_list = enrich_dataframe_with_images_list(
        article_url=review_url,
        out_dir=location_info,
        download=True,
        store_json=True,
        screenshot_tweets=True
    )
    out_df =pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0


