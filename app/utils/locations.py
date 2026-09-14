# Keep this list in sync with the LOCATIONS constant in
# src/app/true-owner/page.tsx on the frontend.

CAMPUS_LOCATIONS: list[str] = [
    "New Block (NB)",
    "Physics UG & PG Block",
    "Chemistry UG & PG Block",
    "Rahda Thiagarajar Auditorium (RTA)",
    "Zoology Block (NH)",
    "Biotechnology Block",
    "Library Block",
    "TK Block",
    "Others",
]

OTHERS_LOCATION = "Others"


def format_location(location: str | None, location_detail: str | None) -> str | None:
    """Combine the picked dropdown value with the free-text detail (only used for 'Others')."""
    if not location:
        return None
    if location == OTHERS_LOCATION and location_detail and location_detail.strip():
        return f"{OTHERS_LOCATION} - {location_detail.strip()}"
    return location


def validate_location(item_type: str, location: str | None, location_detail: str | None) -> str | None:
    """
    Returns an error message string if invalid, otherwise None.
    Rule: location is OPTIONAL for 'lost' reports, COMPULSORY for 'found' reports
    (the finder must say exactly where they found it). If 'Others' is picked, the
    free-text detail is required only for 'found' reports (still optional for 'lost').
    """
    if item_type == "found":
        if not location or not location.strip():
            return "Location is compulsory for found items — please specify exactly where you found it."
        if location == OTHERS_LOCATION and (not location_detail or not location_detail.strip()):
            return "Please describe the unlisted location where you found the item."
        return None

    # type == "lost": everything about location is optional
    if location and location not in CAMPUS_LOCATIONS:
        return "Unrecognized location selected."
    return None
