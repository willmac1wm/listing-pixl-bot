"""Pricing data and reply templates for Listing Pixl.

Source of truth: HDPhotoHub price list as given by William (Nov 2026).
NEVER quote the retired prices: Essential $295 / Showcase $475 /
Premiere $750. The $250 Jason Ruffenach loyalty rate is never advertised
and is not applied by this bot.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------

PACKAGES = {
    "essential": {
        "key": "essential",
        "name": "Essential",
        "price": 200,
        "sqft": "Up to 1,800 sq ft",
        "max_sqft": 1800,
        "includes": [
            "25-30 HDR photos",
            "professional editing",
            "next-day delivery",
            "MLS-ready",
        ],
        "blurb": "25-30 HDR photos, editing, next-day delivery, MLS-ready. No drone.",
    },
    "standard": {
        "key": "standard",
        "name": "Standard",
        "price": 290,
        "sqft": "Up to 2,800 sq ft",
        "max_sqft": 2800,
        "includes": [
            "35-40 HDR photos",
            "aerial drone photos",
            "professional editing",
            "next-day delivery",
        ],
        "blurb": "35-40 HDR photos plus aerial drone photos.",
    },
    "marketing": {
        "key": "marketing",
        "name": "Marketing",
        "price": 389,
        "sqft": "Up to 3,500 sq ft",
        "max_sqft": 3500,
        "includes": [
            "40-50 HDR photos",
            "10-15 aerial drone photos",
            "2 virtual twilights",
            "2D floor plan",
            "property website",
        ],
        "blurb": "40-50 HDR photos, 10-15 aerials, 2 virtual twilights, 2D floor plan, and a property website.",
    },
    "luxury": {
        "key": "luxury",
        "name": "Luxury",
        "price": 549,
        "sqft": "Up to 5,000 sq ft",
        "max_sqft": 5000,
        "includes": [
            "50-65 HDR photos",
            "15-20 aerial drone photos",
            "cinematic walkthrough video",
            "social media reel",
            "3 virtual twilights",
            "2D floor plan",
            "property website",
        ],
        "blurb": "50-65 HDR photos, 15-20 aerials, cinematic walkthrough, social reel, 3 twilights, floor plan, and website.",
    },
    "signature": {
        "key": "signature",
        "name": "Signature",
        "price": 815,
        "sqft": "Over 5,000 sq ft",
        "max_sqft": None,
        "includes": [
            "65-80+ HDR photos",
            "20-25 aerial drone photos",
            "cinematic walkthrough video",
            "Matterport 3D tour",
            "4 virtual twilights",
            "2D floor plan",
            "property website",
            "social media reel",
            "marketing kit",
        ],
        "blurb": "65-80+ HDR photos, 20-25 aerials, walkthrough, Matterport 3D tour, 4 twilights, floor plan, website, reel, and marketing kit.",
    },
}

ADDONS = {
    "drone": 145,
    "video": 265,
    "matterport": 185,
    "floor plan": 90,
    "twilight": 35,  # per photo
    "staging": 35,  # per photo (virtual staging)
    "marketing kit": 39,
}

MINIMUM_PER_SHOOT = 250
PAIRED_SAME_LOCATION_EACH = 200
VACATION_RENTAL_PRICE = 800  # per property, lifestyle + house shoot, two sessions

WEBSITE = "listingpixl.com"
EMAIL = "ListingPixl.Media@gmail.com"

# ---------------------------------------------------------------------------
# Do-not-engage list
# ---------------------------------------------------------------------------
# If a message appears to be from any of these, the bot replies with
# NOTHING and flags it to William instead. Names are matched
# case-insensitively against the message text and any name the sender
# gives. "Blue Homes Group" is matched as an organisation, not a person.
BLACKLISTED_TERMS = [
    "jen maguire",
    "jennifer maguire",
    "jennifer m. dwyer",
    "jennifer dwyer",
    "jen dwyer",
    "james sofroney",
    "james w. sofroney",
    "sofroney",
    "blue homes",
]

# Towns / areas inside Cape May County, NJ (service area). Used only as a
# rough check — anything unclear is still taken as a lead for William.
CAPE_MAY_COUNTY_PLACES = [
    "cape may", "wildwood", "wildwood crest", "north wildwood",
    "west wildwood", "avalon", "stone harbor", "sea isle",
    "sea isle city", "ocean city", "lower township", "middle township",
    "upper township", "dennis", "dennis township", "woodbine",
    "cape may court house", "court house", "rio grande", "villas",
    "erma", "dias creek", "goshen", "south seaville", "seaville",
    "tuckahoe", "strathmere", "whitesboro", "burleigh",
]

# Places clearly outside Cape May County, for the outside-area flag.
OUTSIDE_AREA_TERMS = [
    "atlantic city", "philadelphia", "philly", "new york", "nyc",
    "cherry hill", "camden", "trenton", "newark", "jersey city",
    "hoboken", "princeton", "edison", "toms river", "brick",
    "atlantic county", "cumberland county", "gloucester county",
    "salem county", "burlington county", "ocean county",
    "margate", "ventnor", "brigantine", "somers point", "linwood",
    "northfield", "egg harbor", "galloway", "hammonton",
    "millville", "vineland", "bridgeton", "salem",
]


def recommend_package(sqft: int | None) -> dict | None:
    """Return the smallest package that fits the square footage."""
    if not sqft:
        return None
    for pkg in PACKAGES.values():
        if pkg["max_sqft"] is None or sqft <= pkg["max_sqft"]:
            return pkg
    return PACKAGES["signature"]


def pricing_summary() -> str:
    """Short price list, suitable for a single text."""
    lines = [
        "Here are our packages:",
        "Essential $200 — up to 1,800 sq ft, 25-30 HDR photos, next-day delivery.",
        "Standard $290 — up to 2,800 sq ft, 35-40 HDR photos + drone photos.",
        "Marketing $389 — up to 3,500 sq ft, 40-50 HDR + 10-15 aerials, 2 twilights, floor plan, website.",
        "Luxury $549 — up to 5,000 sq ft, 50-65 HDR + 15-20 aerials, walkthrough video, reel, 3 twilights, floor plan, website.",
        "Signature $815 — over 5,000 sq ft, 65-80+ HDR + 20-25 aerials, walkthrough, Matterport 3D tour, 4 twilights, floor plan, website, reel, marketing kit.",
        "Add-ons: drone $145, video $265, Matterport $185, floor plan $90, twilight $35/photo, virtual staging $35/photo, marketing kit $39.",
        "Minimum $250 per shoot. Vacation rental / Airbnb shoots are $800 per property.",
    ]
    return " ".join(lines)


def package_detail(key: str) -> str:
    """One-package description."""
    pkg = PACKAGES[key]
    return (
        f"The {pkg['name']} package is ${pkg['price']} ({pkg['sqft']}). "
        f"It includes {pkg['blurb']}"
    )


# ---------------------------------------------------------------------------
# Reply templates — warm, short, plain. No hype, no discounts.
# ---------------------------------------------------------------------------

GREETING = (
    "Hi, thanks for thinking of Listing Pixl! I'm Will's assistant. "
    "What's your name, and is this for a home you're selling or an "
    "Airbnb / vacation rental?"
)

ASK_ADDRESS = (
    "Thanks, {name}! What's the property address?"
)

ASK_DETAILS = (
    "Got it — {address}. How many beds and baths is it, and about how "
    "many square feet, if you know?"
)

ASK_PACKAGE = (
    "{recommendation}Which package are you leaning toward — Essential "
    "($200), Standard ($290), Marketing ($389), Luxury ($549), or "
    "Signature ($815)?"
)

ASK_READINESS = (
    "Great, the {package} package it is. When will the home be ready "
    "to shoot, or when were you hoping to have photos done?"
)

LEAD_COMPLETE = (
    "Thank you! I've passed everything along to Will and he'll be in "
    "touch shortly to confirm next steps. If you need anything else, "
    "just text here."
)

HUMAN_HANDOFF = (
    "Of course — I'll let Will know you'd like to talk with him and "
    "he'll call you back. Is there anything you'd like me to pass along?"
)

ESCALATION_ACK = (
    "Thanks for letting me know — I've passed that along to Will and "
    "he'll follow up with you directly."
)

OUTSIDE_AREA_NOTE = (
    "Just so you know, we shoot in Cape May County, NJ. I'll still pass "
    "your details along to Will in case he can help or point you in the "
    "right direction."
)

STOP_CONFIRMATION = (
    "You're all set — we won't text you again. If you need us later, "
    "just text START. Thanks for thinking of Listing Pixl!"
)

START_CONFIRMATION = (
    "Welcome back! Thanks for thinking of Listing Pixl. Is this for a "
    "home you're selling or an Airbnb / vacation rental?"
)

GOODBYE = (
    "You're welcome! If anything else comes up, just text here and "
    "we'll take care of it."
)

VACATION_RENTAL_NOTE = (
    "Our vacation rental / Airbnb shoot is $800 per property — a "
    "lifestyle shoot plus a house shoot, over two sessions."
)

BOOKING_NO_PROMISE = (
    "I can take down the details and Will will confirm a date and time "
    "with you directly — I can't lock in a shoot time myself."
)
