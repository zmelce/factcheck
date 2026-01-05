
import os, re, json, hashlib
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tf1info-img-scraper/1.5)"}
ALLOWED_EXT = (".jpg", ".jpeg", ".png",".webp", ".avif")
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png","image/webp", "image/avif"}


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

def width_from_candidate(candidate: str) -> int:
    m = re.search(r"\s(\d{3,4})w(\s|$)", candidate)  # " 1280w"
    if m: return int(m.group(1))
    m = re.search(r"/(\d{3,4})(?:/|$)", candidate)    # "/1280/"
    return int(m.group(1)) if m else 0

def _has_class(tag: Tag, needle: str) -> bool:
    classes = tag.get("class", [])
    if isinstance(classes, str):
        classes = [classes]
    return any(needle == c or needle in c for c in classes)

def _is_in_readmore(node: Tag) -> bool:
    """
    Return True if node is inside:
      <section class="... ReadMoreArticle__List ..."> ... </section>
    """
    parent = node if isinstance(node, Tag) else None
    while isinstance(parent, Tag):
        if parent.name == "section" and _has_class(parent, "ReadMoreArticle__List"):
            return True
        parent = parent.parent
    return False

def pick_best_src_from_picture(picture: Tag, base: str) -> str | None:
    best_url, best_w = None, -1
    sources = picture.find_all("source")
    preferred = [s for s in sources if (s.get("type") or "").strip().lower() in ALLOWED_MIME]
    others = [s for s in sources if s not in preferred]

    for group in (preferred, others):
        for source in group:
            srcset = (source.get("srcset") or source.get("data-srcset") or "").strip()
            if not srcset: continue
            for cand in [c.strip() for c in srcset.split(",") if c.strip()]:
                url_token = cand.split()[0]
                u = absurl(base, url_token)
                if not u or not is_allowed_url(u): continue
                w = width_from_candidate(cand)
                if w > best_w: best_w, best_url = w, u
        if best_url: break

    if not best_url:
        img = picture.find("img")
        if img:
            u = img.get("src") or img.get("data-src") or img.get("data-original")
            if u:
                u = absurl(base, u)
                if u and is_allowed_url(u): return u
            ss = img.get("srcset") or img.get("data-srcset")
            if ss:
                cand_best, cand_w = None, -1
                for cand in [c.strip() for c in ss.split(",") if c.strip()]:
                    url_token = cand.split()[0]
                    au = absurl(base, url_token)
                    if not au or not is_allowed_url(au): continue
                    w = width_from_candidate(cand)
                    if w > cand_w: cand_best, cand_w = au, w
                if cand_best: return cand_best
    return best_url


def extract_images_with_context(article_url: str):
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"[fetch error] {article_url}: {e}")
        return []

    base = r.url
    soup = BeautifulSoup(r.text, "html.parser")
    article = soup.select_one("article") or soup

    results = []
    seen_urls = set()

    def add(img_url: str, container: Tag):
        if not img_url or not is_allowed_url(img_url): return
        if img_url in seen_urls: return
        if _is_in_readmore(container):  # <-- NEW: skip “Lire aussi” blocks
            return

        seen_urls.add(img_url)
        caption = ""
        fc = container.find("figcaption")
        if fc: caption = text_clean(fc.get_text(" "))
        if not caption and container.has_attr("aria-label"):
            caption = text_clean(container.get("aria-label"))
        results.append({
            "image_url": img_url,
            "caption": caption,
        })

    for fig in article.select("figure, .Picture, .ArticleImage"):
        if _is_in_readmore(fig):  # <-- NEW: skip entire figure if inside read-more section
            continue
        pic = fig.find("picture")
        if pic:
            u = pick_best_src_from_picture(pic, base)
            if u:
                add(u, fig)
                continue
        img = fig.find("img")
        if img:
            u = img.get("src") or img.get("data-src") or img.get("data-original")
            if u:
                u = absurl(base, u)
                if u and is_allowed_url(u):
                    add(u, fig)
                    continue
            ss = img.get("srcset") or img.get("data-srcset")
            if ss:
                cand_best, cand_w = None, -1
                for cand in [c.strip() for c in ss.split(",") if c.strip()]:
                    url_token = cand.split()[0]
                    au = absurl(base, url_token)
                    if not au or not is_allowed_url(au): continue
                    w = width_from_candidate(cand)
                    if w > cand_w: cand_best, cand_w = au, w
                if cand_best:
                    add(cand_best, fig)

    for pic in article.find_all("picture"):
        if pic.find_parent("figure"):
            continue
        if _is_in_readmore(pic):  # <-- NEW
            continue
        u = pick_best_src_from_picture(pic, base)
        if u:
            add(u, pic)

    for img in article.find_all("img"):
        if img.find_parent("figure") or img.find_parent("picture"):
            continue
        if _is_in_readmore(img):  # <-- NEW
            continue
        u = img.get("src") or img.get("data-src") or img.get("data-original")
        if u:
            u = absurl(base, u)
            if u and is_allowed_url(u):
                add(u, img)
                continue
        ss = img.get("srcset") or img.get("data-srcset")
        if ss:
            cand_best, cand_w = None, -1
            for cand in [c.strip() for c in ss.split(",") if c.strip()]:
                url_token = cand.split()[0]
                au = absurl(base, url_token)
                if not au or not is_allowed_url(au): continue
                w = width_from_candidate(cand)
                if w > cand_w: cand_best, cand_w = au, w
            if cand_best:
                add(cand_best, img)

    return results


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
                if not path:
                    continue


                images.append({
                    "image_url": it["image_url"],
                    "caption": it.get("caption", ""),
                    "path": os.path.basename(path),
                })
        else:
            images = items

    return images

def handle(review_url: str, location_info: str):
    out_list = enrich_dataframe_with_images_list(
        article_url=review_url,
        out_dir=location_info,
        download=True,
        store_json=True
    )
    out_df =pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0

