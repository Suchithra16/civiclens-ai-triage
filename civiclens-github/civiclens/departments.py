"""
CivicLens - municipal departments.

One department per complaint category. The key (e.g. "roads") is the category
the AI picks; routing to the department is done in code so it is always consistent.
"""

DEPARTMENTS = {
    "roads": {
        "name": "Roads & Engineering Department",
        "icon": "🛣️",
        "description": "Builds and maintains city roads, footpaths and public road "
                       "infrastructure, and repairs damage reported by citizens.",
        "handles": ["Potholes", "Damaged roads", "Broken footpaths", "Speed breakers"],
    },
    "waste": {
        "name": "Solid Waste Management Department",
        "icon": "🗑️",
        "description": "Runs door-to-door garbage collection, street sweeping and "
                       "community bins, and acts against illegal dumping.",
        "handles": ["Uncollected garbage", "Overflowing bins", "Illegal dumping", "Litter"],
    },
    "water": {
        "name": "Water Supply Department",
        "icon": "🚰",
        "description": "Supplies drinking water to homes and public taps, and maintains "
                       "the city's water pipelines.",
        "handles": ["No water supply", "Pipeline leaks", "Dirty or contaminated water", "Low pressure"],
    },
    "electricity": {
        "name": "Street Lighting & Electrical Department",
        "icon": "💡",
        "description": "Maintains streetlights and electrical fixtures on public roads. "
                       "Faults on main power lines are coordinated with the power company.",
        "handles": ["Broken streetlights", "Damaged poles", "Hanging or exposed wires"],
    },
    "trees": {
        "name": "Horticulture & Tree Cell",
        "icon": "🌳",
        "description": "Looks after trees, parks and green cover; removes fallen trees "
                       "and prunes dangerous branches.",
        "handles": ["Fallen trees", "Dangerous branches", "Pruning requests", "Tree cutting"],
    },
    "drainage": {
        "name": "Drainage & Sewerage Department",
        "icon": "🌊",
        "description": "Keeps storm-water drains and sewer lines flowing, and repairs "
                       "manholes to prevent flooding and sewage overflow.",
        "handles": ["Blocked drains", "Sewage overflow", "Open or broken manholes", "Waterlogging"],
    },
    "other": {
        "name": "Public Grievance Cell",
        "icon": "📋",
        "description": "Reviews complaints that don't fit any single department and "
                       "forwards them to the right office.",
        "handles": ["Everything else"],
    },
}

CATEGORIES = list(DEPARTMENTS)


def department_name(category):
    return DEPARTMENTS.get(category, DEPARTMENTS["other"])["name"]
