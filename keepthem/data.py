"""Synthetic company data (100% made up - no real people).

The company is "Northwind Labs", an engineering org of 32 people (29 active, 3 departed)
in 5 teams. "Today" is config.AS_OF (2026-10-01).

Built-in story (what the agent should discover):
  * Platform is a hotspot. Its manager, Marcus Webb, has 9 direct reports because Director
    Helen Brooks denied his backfill request.
  * Priya Nair and Tom Becker (Platform) resigned this summer, citing on-call load, a
    stalled promotion and manager bandwidth. Both worked on the cancelled "Orion Migration".
  * Dana Ruiz, a senior engineer whose skills are hard to replace, is the next domino:
    review score fell from 4.4 to 2.6, promotion denied, complaint filed, on the same
    cancelled project as both leavers, peer of the leaver, paid below same-role peers.
  * Victor Alvarez and Sam Okoye are secondary risks. Payments and Data are healthy.

Tuple layouts are documented next to each table. Keep the file declarative: tweaking the
demo should only mean editing data here, never the loader.
"""

# ---- Teams -----------------------------------------------------------------------
TEAMS = [  # (id, name, function)
    ("T-lead", "Eng Leadership", "Leadership"),
    ("T-platform", "Platform", "Infrastructure"),
    ("T-payments", "Payments", "Product Engineering"),
    ("T-growth", "Growth", "Product Engineering"),
    ("T-data", "Data", "Data & ML"),
]

# ---- Employees ---------------------------------------------------------------------
# (id, name, role, team_id, manager_id, tenure_months, location, salary_band 1-7, status)
# `tenure_months` for departed staff = tenure at the time they left.
EMPLOYEES = [
    ("E001", "Helen Brooks", "Director of Engineering", "T-lead", None, 96, "San Francisco", 7, "active"),
    ("E002", "Marcus Webb", "Engineering Manager", "T-platform", "E001", 60, "Austin", 5, "active"),
    ("E003", "Elena Costa", "Engineering Manager", "T-payments", "E001", 48, "Lisbon", 5, "active"),
    ("E004", "Jin Park", "Engineering Manager", "T-growth", "E001", 40, "Seoul", 5, "active"),
    ("E005", "Aisha Rahman", "Data Manager", "T-data", "E001", 54, "London", 5, "active"),
    # Platform (reports to Marcus Webb: 9 active direct reports)
    ("E006", "Dana Ruiz", "Senior Software Engineer", "T-platform", "E002", 42, "Austin", 4, "active"),
    ("E007", "Sam Okoye", "Senior Software Engineer", "T-platform", "E002", 30, "Remote-US", 5, "active"),
    ("E008", "Leila Haddad", "Software Engineer", "T-platform", "E002", 22, "Berlin", 3, "active"),
    ("E009", "Omar Farouk", "Software Engineer", "T-platform", "E002", 18, "Remote-EMEA", 3, "active"),
    ("E010", "Grace Liu", "Staff Engineer", "T-platform", "E002", 70, "Austin", 6, "active"),
    ("E011", "Victor Alvarez", "Site Reliability Engineer", "T-platform", "E002", 36, "Austin", 4, "active"),
    ("E012", "Hannah Berg", "Software Engineer", "T-platform", "E002", 10, "Berlin", 2, "active"),
    ("E013", "Ravi Menon", "Senior Software Engineer", "T-platform", "E002", 52, "Bangalore", 5, "active"),
    ("E014", "Chloe Martin", "Software Engineer", "T-platform", "E002", 27, "Toronto", 3, "active"),
    ("E015", "Priya Nair", "Senior Software Engineer", "T-platform", "E002", 45, "Austin", 4, "left"),
    ("E016", "Tom Becker", "Site Reliability Engineer", "T-platform", "E002", 38, "Remote-US", 4, "left"),
    # Payments (reports to Elena Costa)
    ("E017", "Noah Fischer", "Senior Software Engineer", "T-payments", "E003", 44, "Lisbon", 5, "active"),
    ("E018", "Mia Rossi", "Software Engineer", "T-payments", "E003", 20, "Milan", 3, "active"),
    ("E019", "Kenji Tanaka", "Software Engineer", "T-payments", "E003", 26, "Remote-APAC", 3, "active"),
    ("E020", "Sofia Petrova", "Staff Engineer", "T-payments", "E003", 66, "Lisbon", 6, "active"),
    ("E021", "Ben Carter", "Software Engineer", "T-payments", "E003", 8, "London", 2, "active"),
    # Growth (reports to Jin Park)
    ("E022", "Aaliyah Grant", "Senior Software Engineer", "T-growth", "E004", 34, "Remote-US", 4, "active"),
    ("E023", "Lucas Meyer", "Software Engineer", "T-growth", "E004", 15, "Berlin", 3, "active"),
    ("E024", "Zoe Kim", "Product Engineer", "T-growth", "E004", 28, "Seoul", 3, "active"),
    ("E025", "Diego Morales", "Software Engineer", "T-growth", "E004", 12, "Mexico City", 2, "active"),
    ("E026", "Fatima Khan", "Senior Software Engineer", "T-growth", "E004", 50, "London", 5, "active"),
    ("E027", "Ethan Wright", "Software Engineer", "T-growth", "E004", 21, "Austin", 3, "active"),
    ("E028", "Marta Silva", "Software Engineer", "T-growth", "E004", 24, "Lisbon", 3, "left"),
    # Data (reports to Aisha Rahman)
    ("E029", "Yusuf Demir", "Data Scientist", "T-data", "E005", 38, "Istanbul", 4, "active"),
    ("E030", "Isabel Cruz", "Data Engineer", "T-data", "E005", 17, "Madrid", 3, "active"),
    ("E031", "Arjun Patel", "Senior Data Engineer", "T-data", "E005", 48, "Bangalore", 5, "active"),
    ("E032", "Nina Kowalski", "Data Analyst", "T-data", "E005", 14, "Warsaw", 2, "active"),
]

# Date a departed employee left.
LEFT_DATES = {"E015": "2026-07-18", "E016": "2026-08-29", "E028": "2026-05-30"}

# ---- Projects ----------------------------------------------------------------------
PROJECTS = [  # (id, name, status, team_id)
    ("P01", "Orion Migration", "cancelled", "T-platform"),
    ("P02", "Atlas API Gateway", "active", "T-platform"),
    ("P03", "Pulse Observability", "active", "T-platform"),
    ("P04", "Ledger v2", "active", "T-payments"),
    ("P05", "Checkout Revamp", "active", "T-payments"),
    ("P06", "Referral Engine", "active", "T-growth"),
    ("P07", "Experimentation Platform", "active", "T-growth"),
    ("P08", "Lakehouse", "active", "T-data"),
    ("P09", "Insights Dashboard", "completed", "T-data"),
    ("P10", "Zero-Trust Rollout", "active", "T-platform"),
]

WORKED_ON = {  # project_id -> employee ids
    "P01": ["E006", "E007", "E011", "E013", "E015", "E016"],
    "P02": ["E008", "E009", "E010", "E012", "E013", "E014"],
    "P03": ["E006", "E010", "E011", "E012", "E014"],
    "P10": ["E007", "E010"],
    "P04": ["E017", "E018", "E019", "E020"],
    "P05": ["E017", "E018", "E021"],
    "P06": ["E022", "E023", "E024", "E028"],
    "P07": ["E025", "E026", "E027", "E024", "E028"],
    "P08": ["E029", "E030", "E031", "E032"],
    "P09": ["E029", "E030", "E032"],
}

# ---- Skills --------------------------------------------------------------------------
SKILLS = [  # (id, name, market_demand)
    ("S-go", "Go", "high"), ("S-k8s", "Kubernetes", "high"), ("S-rust", "Rust", "high"),
    ("S-kafka", "Kafka", "high"), ("S-ds", "Distributed Systems", "high"),
    ("S-sec", "Security", "high"), ("S-ml", "Machine Learning", "high"),
    ("S-py", "Python", "medium"), ("S-tf", "Terraform", "medium"), ("S-react", "React", "medium"),
    ("S-ts", "TypeScript", "medium"), ("S-spark", "Spark", "medium"), ("S-obs", "Observability", "medium"),
    ("S-exp", "Experimentation", "medium"), ("S-pg", "PostgreSQL", "low"), ("S-sql", "SQL", "low"),
    ("S-java", "Java", "low"),
]
EMPLOYEE_SKILLS = {
    "E001": ["S-ds"], "E002": ["S-go", "S-k8s"], "E003": ["S-java", "S-kafka"], "E004": ["S-ts", "S-exp"],
    "E005": ["S-spark", "S-ml"],
    "E006": ["S-go", "S-k8s", "S-ds", "S-rust"],     # Dana: sole holder of Rust & Distributed Systems on Platform
    "E007": ["S-go", "S-k8s", "S-sec"], "E008": ["S-go", "S-py"], "E009": ["S-go", "S-tf"],
    "E010": ["S-go", "S-k8s", "S-sec", "S-tf"], "E011": ["S-k8s", "S-tf", "S-obs"], "E012": ["S-py", "S-obs"],
    "E013": ["S-go", "S-pg", "S-kafka"], "E014": ["S-py", "S-tf"], "E015": ["S-go", "S-ds"], "E016": ["S-k8s", "S-obs"],
    "E017": ["S-java", "S-kafka", "S-pg"], "E018": ["S-java", "S-pg"], "E019": ["S-java", "S-react"],
    "E020": ["S-go", "S-ds", "S-kafka"], "E021": ["S-java"],
    "E022": ["S-ts", "S-react", "S-exp"], "E023": ["S-ts", "S-react"], "E024": ["S-react", "S-exp"],
    "E025": ["S-ts"], "E026": ["S-ts", "S-pg", "S-exp"], "E027": ["S-py", "S-ts"], "E028": ["S-ts", "S-react"],
    "E029": ["S-py", "S-ml", "S-sql"], "E030": ["S-py", "S-spark", "S-sql"], "E031": ["S-spark", "S-sql", "S-kafka"],
    "E032": ["S-sql", "S-py"],
}

# ---- Reviews ---------------------------------------------------------------------------
REVIEW_DATES = ["2025-04-30", "2025-10-31", "2026-04-30"]
# employee -> scores (1-5), aligned to the *last* len(scores) REVIEW_DATES (new hires have fewer).
REVIEW_SCORES = {
    "E001": [4.5, 4.5, 4.4], "E002": [4.0, 4.1, 3.8], "E003": [4.3, 4.4, 4.5], "E004": [3.9, 4.0, 4.1],
    "E005": [4.2, 4.2, 4.3],
    "E006": [4.6, 4.4, 2.6], "E007": [4.0, 3.8, 3.4], "E008": [3.6, 3.8, 3.9], "E009": [3.2, 3.0, 3.1],
    "E010": [4.7, 4.6, 4.8], "E011": [3.5, 3.2, 2.9], "E012": [3.5], "E013": [4.1, 4.0, 4.0],
    "E014": [3.7, 3.8, 3.6], "E015": [4.5, 4.3, 3.0], "E016": [3.8, 3.4, 2.8],
    "E017": [4.2, 4.3, 4.4], "E018": [3.9, 4.1, 4.2], "E019": [3.6, 3.7, 3.8], "E020": [4.6, 4.7, 4.7],
    "E021": [3.4],
    "E022": [4.0, 3.9, 3.8], "E023": [3.6, 3.7], "E024": [3.8, 3.9, 3.9], "E025": [3.4, 3.5],
    "E026": [4.4, 4.5, 4.4], "E027": [3.9, 3.5, 3.2], "E028": [3.6, 3.4, 3.2],
    "E029": [4.0, 4.1, 4.2], "E030": [3.9, 4.0], "E031": [4.5, 4.5, 4.6], "E032": [3.6, 3.7],
}
# Hand-written review summaries; everything else gets a generic line chosen by score.
REVIEW_SUMMARIES = {
    ("E006", "2026-04-30"): "Delivery strong but ownership concerns; missed Q1 goals after Orion scope was cut. "
                            "Review written under heavy manager workload.",
    ("E006", "2025-10-31"): "Exceptional technical leadership on Orion; mentors the team.",
    ("E015", "2026-04-30"): "Disengaged since promotion was deferred; output dropped.",
    ("E016", "2026-04-30"): "On-call fatigue visible; reliability metrics slipped.",
}

# ---- Events ---------------------------------------------------------------------------
# (employee_id, type, date, description). Resignations and peer_left events are generated
# automatically from LEFT_DATES + PEER_OF in the loader.
EVENTS = [
    ("E006", "promotion", "2025-03-01", "Promoted to Senior Software Engineer."),
    ("E006", "project_cancelled", "2026-06-12", "Orion Migration cancelled; Dana reassigned to Pulse maintenance."),
    ("E006", "complaint", "2026-07-02", "Told HRBP the on-call rotation is unsustainable with the current headcount."),
    ("E007", "project_cancelled", "2026-06-12", "Orion Migration cancelled."),
    ("E007", "promotion", "2025-09-01", "Promoted to Senior Software Engineer."),
    ("E011", "overtime_spike", "2026-08-15", "On-call pages doubled after Tom Becker left."),
    ("E011", "project_cancelled", "2026-06-12", "Orion Migration cancelled."),
    ("E013", "project_cancelled", "2026-06-12", "Orion Migration cancelled."),
    ("E010", "kudos", "2026-05-01", "Recognised for the Zero-Trust design review."),
    ("E010", "promotion", "2025-06-01", "Promoted to Staff Engineer."),
    ("E008", "kudos", "2026-03-15", "Great launch of the gateway rate limiter."),
    ("E013", "promotion", "2024-03-01", "Promoted to Senior Software Engineer."),
    ("E015", "complaint", "2026-03-25", "Raised promotion and workload concerns with HRBP."),
    ("E016", "complaint", "2026-06-05", "Asked for a transfer to Data; escalated lack of manager bandwidth."),
    ("E027", "manager_change", "2026-03-01", "Moved to Jin Park after the Growth reorg."),
    ("E027", "reorg", "2026-03-01", "Growth team reorganised; project ownership changed."),
    ("E017", "promotion", "2026-01-15", "Promoted to Senior Software Engineer."),
    ("E020", "promotion", "2025-02-01", "Promoted to Staff Engineer."),
    ("E020", "kudos", "2026-04-02", "Led the Ledger v2 data-model rewrite."),
    ("E026", "promotion", "2025-06-01", "Promoted to Senior Software Engineer."),
    ("E022", "promotion", "2025-04-01", "Promoted to Senior Software Engineer."),
    ("E029", "promotion", "2025-11-01", "Promoted to Data Scientist II."),
    ("E031", "promotion", "2025-12-01", "Promoted to Senior Data Engineer."),
    ("E031", "kudos", "2026-02-10", "Shipped the Lakehouse ingestion framework."),
    ("E018", "kudos", "2026-05-20", "Checkout latency reduced by 30%."),
    ("E024", "promotion", "2025-05-01", "Promoted to Product Engineer."),
    ("E002", "promotion", "2024-12-01", "Promoted to Engineering Manager."),
    ("E003", "promotion", "2025-03-01", "Promoted to Engineering Manager."),
    ("E004", "promotion", "2025-09-01", "Promoted to Engineering Manager."),
    ("E005", "promotion", "2025-01-01", "Promoted to Data Manager."),
    ("E001", "promotion", "2025-02-01", "Promoted to Director of Engineering."),
]
RESIGNATION_TEXT = {
    "E015": "Resigned for a competitor. Cited on-call load and a promotion that was deferred twice.",
    "E016": "Resigned for a startup. Cited manager bandwidth and denied transfer.",
    "E028": "Resigned. Cited unclear ownership after the Growth reorg and a denied raise.",
}

# ---- Decisions -------------------------------------------------------------------------
# (id, maker_id, affected_id, type, date, outcome, reason)
DECISIONS = [
    ("D-001", "E002", "E006", "promotion_denied", "2026-04-15", "denied",
     "Staff promotion deferred: no capacity to run calibration this cycle."),
    ("D-002", "E002", "E015", "promotion_denied", "2026-03-20", "denied",
     "Promotion deferred a second time."),
    ("D-003", "E002", "E006", "project_removal", "2026-06-12", "reassigned",
     "Removed from Orion on cancellation; no replacement scope offered."),
    ("D-004", "E002", "E011", "raise_denied", "2026-05-10", "denied",
     "Budget frozen for Platform this cycle."),
    ("D-005", "E002", "E016", "transfer_denied", "2026-06-01", "denied",
     "Cannot release headcount."),
    ("D-006", "E003", "E017", "promotion_granted", "2026-01-15", "approved", "Consistent senior-level impact."),
    ("D-007", "E003", "E020", "raise_approved", "2026-03-01", "approved", "Market adjustment."),
    ("D-008", "E004", "E028", "raise_denied", "2026-03-10", "denied", "Outside review cycle."),
    ("D-009", "E005", "E031", "promotion_granted", "2025-12-01", "approved", "Led Lakehouse ingestion."),
    ("D-010", "E001", "E002", "headcount_denied", "2026-05-20", "denied",
     "Platform backfill request denied: hiring freeze."),
]

# ---- PEER_OF edges (stored once, queried undirected) ---------------------------------------
PEERS = [
    ("E006", "E015"), ("E006", "E007"), ("E006", "E011"), ("E006", "E010"), ("E006", "E020"),
    ("E015", "E016"), ("E015", "E007"), ("E016", "E011"), ("E008", "E009"), ("E008", "E014"),
    ("E009", "E012"), ("E013", "E010"), ("E013", "E011"), ("E014", "E012"), ("E007", "E013"),
    ("E017", "E018"), ("E018", "E019"), ("E019", "E021"), ("E017", "E020"), ("E018", "E021"),
    ("E022", "E023"), ("E023", "E024"), ("E024", "E025"), ("E026", "E022"), ("E027", "E028"),
    ("E027", "E024"), ("E028", "E025"), ("E027", "E026"),
    ("E029", "E030"), ("E030", "E031"), ("E031", "E032"), ("E032", "E029"),
    ("E002", "E003"), ("E003", "E004"), ("E004", "E005"), ("E002", "E004"),
]
