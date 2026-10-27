"""Static reference lists for synthetic data. Every name here is made up.

Real countries are all LOW risk on purpose; the HIGH/MEDIUM jurisdictions are
fictional so the demo never labels a real country as risky.
"""

FIRST_NAMES = [
    "Avery", "Jordan", "Morgan", "Riley", "Casey", "Quinn", "Taylor", "Reese", "Parker", "Rowan",
    "Elena", "Marcus", "Priya", "Daniel", "Sofia", "Andre", "Grace", "Luis", "Hannah", "Omar",
    "Naomi", "Wesley", "Ingrid", "Tobias", "Camila", "Desmond", "Leah", "Victor", "Mei", "Felix",
]

LAST_NAMES = [
    "Holloway", "Marsh", "Delgado", "Okafor", "Brennan", "Nakamura", "Whitfield", "Castellano",
    "Pruitt", "Lindqvist", "Abernathy", "Reyes", "Kowalski", "Fairbanks", "Mbeki", "Sutherland",
    "Ashford", "Valdez", "Crane", "Haddad", "Thornton", "Ivers", "Calloway", "Petrov", "Gallagher",
]

HOME_CITIES = [
    "Charlotte, NC", "Raleigh, NC", "Durham, NC", "Greensboro, NC", "Charleston, SC",
    "Columbia, SC", "Atlanta, GA", "Richmond, VA", "Nashville, TN",
]

RETAIL_OCCUPATIONS = [
    "Registered nurse", "Software developer", "Teacher", "Retail associate", "Electrician",
    "Accountant", "Warehouse supervisor", "Sales representative", "Pharmacist", "Graduate student",
    "Retired", "Truck driver", "Dental hygienist", "Marketing coordinator",
]

SMALL_BUSINESS_TYPES = [
    "Bakery", "Auto repair shop", "Hair salon", "IT consulting", "Florist", "Food truck",
    "Dry cleaner", "Bookkeeping services", "Pet grooming",
]

EMPLOYERS = [
    "Piedmont Health System", "Queen City Logistics", "Carolina Unified Schools",
    "Blue Ridge Software", "Southeast Power Co", "Triangle Biotech", "Harbor Retail Group",
]

CARD_MERCHANTS = [
    "Queen City Grocers", "Piedmont Fuel", "Uptown Coffee Co", "Lakeside Pharmacy",
    "Main Street Hardware", "Crescent Diner", "Metro Transit", "Riverbend Cinemas",
    "Northgate Pet Supply", "Southpark Apparel", "Oak Lane Market", "StreamBox Subscription",
]

BILLERS = ["Duke Street Utilities", "Carolina Wireless", "Summit Auto Insurance", "Ridgeview Apartments"]

FOREIGN_CITIES = [
    ("Lisbon", "Portugal"), ("Toronto", "Canada"), ("London", "United Kingdom"),
    ("Tokyo", "Japan"), ("Bucharest", "Romania"), ("Manila", "Philippines"),
    ("Sao Paulo", "Brazil"), ("Berlin", "Germany"),
]

HIGH_RISK_JURISDICTIONS = ["Corvania", "Valtessa", "Port Meridia"]
MEDIUM_RISK_JURISDICTIONS = ["Austral Keys"]

SHELL_COUNTERPARTIES = [
    "Global Trade Partners LLC", "Apex Meridian Holdings", "Northstar Commodities Ltd",
    "Silverline Ventures SA", "Bluewater Consulting Group",
]

ANALYSTS = [f"analyst_{i:02d}" for i in range(1, 7)]


def jurisdiction_rows() -> list[tuple[str, str, str]]:
    """(country, risk_tier, note) rows for the jurisdiction_risk table."""
    rows = [("United States", "LOW", "Domestic")]
    rows += [(country, "LOW", "Standard due diligence") for _, country in FOREIGN_CITIES]
    rows += [(c, "HIGH", "Fictional jurisdiction - enhanced due diligence required")
             for c in HIGH_RISK_JURISDICTIONS]
    rows += [(c, "MEDIUM", "Fictional jurisdiction - heightened monitoring")
             for c in MEDIUM_RISK_JURISDICTIONS]
    return rows
