
import os, re, json, hashlib
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; 20mn-img-scraper/1.3)"}
IMG_EXT  = (".jpg", ".jpeg", ".png", ".webp")  # SAME as your previous working code
SCOPE_SEL = "article.o-paper__content figure.c-media div.c-media__content"


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
        if len(p) > 1 and p[1].endswith("w"):
            try:
                w = int(p[1][:-1])
            except:
                w = 0
        if u and w > best_w:
            best_u, best_w = u, w
    return best_u

def _clean_text(t):
    return re.sub(r"\s+", " ", (t or "").strip())

def get_ext(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]


def extract_images_with_captions(article_url):
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"[fetch error] {article_url}: {e}")
        return []

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except Exception:
        soup = BeautifulSoup(r.text, "html.parser")

    base = r.url
    boxes = soup.select(SCOPE_SEL)

    candidates = []       # raw URLs (like before)
    captions_by_box = []  # parallel list of captions (from the box's figure)

    for box in boxes:
        cap = ""
        fig = box.find_parent("figure")
        if fig:
            fc = fig.find("figcaption")
            if fc:
                cap = _clean_text(fc.get_text(" "))

        for pic in box.find_all("picture"):
            for src in pic.find_all("source"):
                ss = src.get("srcset") or src.get("data-srcset")
                if ss:
                    u = pick_largest_from_srcset(ss, base)
                    if u:
                        candidates.append(u)
                        captions_by_box.append(cap)

            img = pic.find("img")
            if img:
                u = img.get("src") or img.get("data-src") or img.get("data-original")
                if not u:
                    ss = img.get("srcset") or img.get("data-srcset")
                    if ss:
                        u = pick_largest_from_srcset(ss, base)
                if u:
                    u = absurl(base, u)
                    if u:
                        candidates.append(u)
                        captions_by_box.append(cap)

        for img in box.find_all("img"):
            if img.find_parent("picture"):
                continue
            u = img.get("src") or img.get("data-src") or img.get("data-original")
            if not u:
                ss = img.get("srcset") or img.get("data-srcset")
                if ss:
                    u = pick_largest_from_srcset(ss, base)
            if u:
                u = absurl(base, u)
                if u:
                    candidates.append(u)
                    captions_by_box.append(cap)

    seen = set()
    out = []
    for u, cap in zip(candidates, captions_by_box):
        if not u:
            continue
        ext = get_ext(u)
        if ext and ext not in IMG_EXT:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append({"image_url": u, "caption": cap})

    return out


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
        fpath = os.path.join(dest_dir, f"{prefix}_{h}{ext}")
        with open(fpath, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk: f.write(chunk)
        return fpath
    except Exception as e:
        return None

def enrich_dataframe_with_images_list(article_url="reviewURL",
                                      out_dir="20minutes_images",
                                      download=True,
                                      store_json=True):

    images = []

    if article_url:
        items = extract_images_with_captions(article_url)  # [{"image_url","caption"}...]
        if download and items:
            prefix = safe_slug(article_url)
            for it in items:
                path = download_image(it["image_url"], out_dir, f"{prefix}_img")
                images.append({
                    "image_url": it["image_url"],
                    "caption": it.get("caption", ""),
                    "path": path
                })
        else:
            images = items

    return images

def handle(review_url: str, location_info: str):
    out_list = enrich_dataframe_with_images_list(
        article_url=review_url,
        out_dir=location_info,
        download=True,
        store_json=True,
    )
    out_df =pd.DataFrame(out_list)
    csv_path = os.path.join(location_info, "image_info.csv")
    out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0

