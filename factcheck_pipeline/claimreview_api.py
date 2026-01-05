from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests

FACTCHECK_API = "https://factchecktools.googleapis.com/v1alpha1/claims:search"


@dataclass
class ClaimReviewItem:
    claim_text: str
    claimant: str
    claim_date: str
    review_url: str
    review_title: str
    review_date: str
    review_label_raw: str
    publisher_name: str
    publisher_site: str


def norm_site(site: str) -> str:
    s = (site or "").strip().lower()
    if not s:
        return ""

    if not s.startswith(("http://", "https://")):
        s_for_parse = "https://" + s
    else:
        s_for_parse = s

    host = (urlsplit(s_for_parse).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def fetch_claimreview_claims(
    api_key: str,
    review_publisher_site_filter: str,
    page_size: int = 1,
    language_code: Optional[str] = None,
    sleep_s: float = 0.2,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "key": api_key,
        "reviewPublisherSiteFilter": review_publisher_site_filter,
        "pageSize": page_size,
    }
    if language_code:
        params["languageCode"] = language_code

    all_claims: List[Dict[str, Any]] = []
    next_token: Optional[str] = None

    while True:
        if next_token:
            params["pageToken"] = next_token
        else:
            params.pop("pageToken", None)

        r = requests.get(FACTCHECK_API, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()

        all_claims.extend(data.get("claims", []))
        next_token = data.get("nextPageToken")
        if not next_token:
            break

        time.sleep(sleep_s)

    return all_claims


def flatten_claims(
    claims: List[Dict[str, Any]],
    *,
    review_publisher_site_filter: Optional[str] = None,
) -> List[ClaimReviewItem]:

    wanted = norm_site(review_publisher_site_filter or "")

    items: List[ClaimReviewItem] = []
    for claim in claims:
        claim_text = claim.get("text", "") or ""
        claimant = claim.get("claimant", "") or ""
        claim_date = claim.get("claimDate", "") or ""

        for review in claim.get("claimReview", []) or []:
            pub_obj = (review.get("publisher") or {})
            publisher_name = pub_obj.get("name", "") or ""
            publisher_site = pub_obj.get("site", "") or ""


            if wanted and norm_site(publisher_site) != wanted:
                continue

            items.append(
                ClaimReviewItem(
                    claim_text=claim_text,
                    claimant=claimant,
                    claim_date=claim_date,
                    review_url=review.get("url", "") or "",
                    review_title=review.get("title", "") or "",
                    review_date=review.get("reviewDate", "") or "",
                    review_label_raw=review.get("textualRating", "") or "",
                    publisher_name=publisher_name,
                    publisher_site=publisher_site,
                )
            )

    return items
