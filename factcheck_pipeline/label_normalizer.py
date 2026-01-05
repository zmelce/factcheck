def normalize_label(textual_rating: str) -> str:
    r = (textual_rating or "").strip().lower()

    if r in {"true", "wahr", "stimmt"}:
        return "True"
    if r in {"false", "falsch", "stimmt nicht"}:
        return "False"
    if "teil" in r or "part" in r or "teilweise" in r:
        return "Partly-True"

    return "Other"
