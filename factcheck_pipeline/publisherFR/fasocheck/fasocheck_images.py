
import os, re, json, hashlib
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup, Tag
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fasocheck-img-scraper/1.2)"}
IMG_EXT  = (".jpg", ".jpeg", ".png", ".webp",".avif")


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

def clean_text(t):
    return re.sub(r"\s+", " ", (t or "").strip())

def get_ext(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]

def is_img_url(u: str) -> bool:
    ext = get_ext(u)
    if not ext:
        return False
    return (ext in IMG_EXT)

def pick_largest_from_srcset(srcset: str, base: str) -> str | None:
    best_u, best_w = None, -1
    for part in str(srcset).split(","):
        p = part.strip().split()
        if not p:
            continue
        u = absurl(base, p[0])
        w = 0
        if len(p) > 1 and p[1].endswith("w"):
            try: w = int(p[1][:-1])
            except: w = 0
        if u and w > best_w:
            best_u, best_w = u, w
    return best_u


def caption_from_wp_caption(img: Tag) -> str | None:
    if not img:
        return None

    wp = img.find_parent(class_=lambda c: c and "wp-caption" in (c if isinstance(c, list) else [c]))
    if not wp:
        return None

    desc_id = (img.get("aria-describedby") or "").strip()
    if desc_id:
        cap_el = wp.find(id=desc_id)
        if cap_el:
            return clean_text(cap_el.get_text(" ", strip=True)) or None

    el = wp.find("p", class_=lambda c: c and "wp-caption-text" in (c if isinstance(c, list) else [c]))
    if el:
        return clean_text(el.get_text(" ", strip=True)) or None

    return None

def caption_from_figcaption(img: Tag) -> str | None:
    fig = img.find_parent("figure")
    if fig:
        fc = fig.find("figcaption")
        if fc:
            cap = clean_text(fc.get_text(" ", strip=True))
            return cap or None
    return None

def caption_from_next_p(img: Tag) -> str | None:
    p = img.find_parent("p")
    if not p:
        return None
    nxt = p.find_next_sibling()
    while nxt is not None and getattr(nxt, "name", None) is None:
        nxt = nxt.next_sibling
    if nxt and nxt.name == "p":
        txt = clean_text(nxt.get_text(" ", strip=True))
        return txt or None
    return None

def caption_fallback_alt(img: Tag) -> str | None:
    alt = clean_text(img.get("alt", ""))
    if alt and alt.lower() not in {"", "image", "photo", "img"}:
        return alt
    return None


def extract_images_with_captions(article_url: str):
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"[fetch error] {article_url}: {e}")
        return []

    html = r.text
    try:
        soup = BeautifulSoup(html, "lxml")
    except:
        soup = BeautifulSoup(html, "html.parser")

    base = r.url

    scope = soup.select_one("div.entry-content")
    if not scope:
        return []

    for sel in [
        ".sfsi_responsive_icons", ".sfsiaftrpstwpr",
        "section#comment-wrap", "#comment-wrap",
        ".sfsi_icons_container", ".sfsi_wicon",
    ]:
        for bad in scope.select(sel):
            bad.decompose()

    out, seen = [], set()

    for img in scope.select("img"):
        u = img.get("src") or img.get("data-src") or ""
        if not u:
            ss = img.get("srcset") or img.get("data-srcset") or ""
            if ss:
                u = pick_largest_from_srcset(ss, base) or ""
        if not u:
            continue

        href = absurl(base, u)
        if not href or not is_img_url(href):
            continue

        key = href.split("?", 1)[0]
        if key in seen:
            continue
        seen.add(key)

        cap = (caption_from_wp_caption(img) or
               caption_from_figcaption(img) or
               caption_from_next_p(img) or
               caption_fallback_alt(img) or
               "")

        out.append({"image_url": href, "caption": cap})

    return out


def safe_slug(s, length=64):
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w.-]+", "_", s).strip("._")
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

def enrich_dataframe_with_images_list(article_url,
                                      out_dir,
                                      download=True):
    images = []
    if article_url:
        items = extract_images_with_captions(article_url)
        if download and items:
            prefix = safe_slug(article_url)
            for it in items:
                path = download_image(it["image_url"], out_dir, f"{prefix}_img")
                image_name = os.path.basename(path)
                images.append({
                    "image_url": it["image_url"],
                    "caption": it.get("caption", "") or "",
                    "path": image_name
                })

    return images


def handle(review_url: str, location_info: str, headless: bool = True):
    out_list = enrich_dataframe_with_images_list(
        article_url=review_url,
        out_dir=location_info,
        download=True,
    )
    out_df = pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
