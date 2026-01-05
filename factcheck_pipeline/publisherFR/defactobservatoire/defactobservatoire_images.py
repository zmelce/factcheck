
import os, re, json, hashlib
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup, Tag
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; afp-factuel-img-scraper/1.0)"}
ALLOWED_EXT  = (".jpg", ".jpeg", ".png", ".webp",".avif")
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/avif"}


def absurl(base, u):
    if not u: return None
    u = u.strip()
    if u.startswith("data:"): return None
    if u.startswith("//"): return "https:" + u
    if u.startswith("http://") or u.startswith("https://"): return u
    return urljoin(base, u)

def get_ext(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]

def is_allowed_url(u: str) -> bool:
    return get_ext(u) in ALLOWED_EXT

def text_clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def extract_from_figures(scope: Tag, base_url: str, seen: set) -> list[dict]:
    out = []
    for fig in scope.select("figure"):
        caption = ""
        fc = fig.find("figcaption")
        if fc:
            caption = text_clean(fc.get_text(" "))

        imgs = []
        for pic in fig.find_all("picture"):
            im = pic.find("img")
            if im: imgs.append(im)
        imgs.extend([im for im in fig.find_all("img") if not im.find_parent("picture")])

        for im in imgs:
            u = im.get("src") or im.get("data-src") or im.get("data-original")
            if not u:
                ss = im.get("srcset") or im.get("data-srcset")
                if ss:
                    cand = ss.split(",")[-1].strip().split()[0]
                    u = cand
            if not u:
                continue
            u = absurl(base_url, u)
            if not u or not is_allowed_url(u) or u in seen:
                continue
            seen.add(u)
            out.append({"image_url": u, "caption": caption})
    return out

def extract_from_wwitem_image(scope: Tag, base_url: str, seen: set) -> list[dict]:
    out = []
    for div in scope.select("div.ww-item.image"):
        for p in div.find_all("p"):
            im = p.find("img")
            if not im:
                continue
            u = im.get("src") or im.get("data-src") or im.get("data-original")
            if not u:
                ss = im.get("srcset") or im.get("data-srcset")
                if ss:
                    cand = ss.split(",")[-1].strip().split()[0]
                    u = cand
            if not u:
                continue
            u = absurl(base_url, u)
            if not u or not is_allowed_url(u) or u in seen:
                continue

            cap_el = p.find("span", class_=lambda c: c and "legend" in c if isinstance(c, str) else c and "legend" in c)
            caption = text_clean(cap_el.get_text(" ")) if cap_el else ""

            seen.add(u)
            out.append({"image_url": u, "caption": caption})
    return out

def extract_from_html(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    scope = soup.select_one("div.defacto-fact-check-body") or soup

    out, seen = [], set()
    out.extend(extract_from_figures(scope, base_url, seen))
    out.extend(extract_from_wwitem_image(scope, base_url, seen))
    return out

def extract_images_with_context(article_url: str):
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return []

    return extract_from_html(r.text, base_url=r.url)


def safe_slug(s, length=64):
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w.-]+", "_", s)
    s = s.strip("._")
    return s[-length:] if len(s) > length else (s or "article")

def download_image(url, dest_dir, prefix):
    if not is_allowed_url(url): return None
    os.makedirs(dest_dir, exist_ok=True)
    try:
        r = requests.get(url, headers=HEADERS, timeout=25, stream=True)
        r.raise_for_status()
        ext = get_ext(url)
        if ext not in ALLOWED_EXT:
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ct not in ALLOWED_MIME: return None
            ext = ".jpg" if "jpeg" in ct else ".png"
        h = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        fpath = os.path.join(dest_dir, f"{h}{ext}")
        with open(fpath, "wb") as f:
            for chunk in r.iter_content(1 << 15):
                if chunk: f.write(chunk)
        return fpath
    except Exception as e:
        return None

def enrich_dataframe_with_images_list(article_url,
                                      out_dir,
                                      download=True,
                                      store_json=True):

    images = []

    if article_url:
        items = extract_images_with_context(article_url)
        if download and items:
            prefix = safe_slug(article_url)
            for it in items:
                path = download_image(it["image_url"], out_dir, f"{prefix}_img")
                image_name = os.path.basename(path)
                it = dict(it)
                it["path"] = path
                images.append({
                    "image_url": it["image_url"],
                    "caption": it.get("caption", ""),
                    "path": image_name
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
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
