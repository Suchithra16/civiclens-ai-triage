"""
Fill CivicLens with sample complaints so the department pages aren't empty
during a demo. Run once:

    python seed_demo.py

To start over, stop the app, delete civiclens.db, and run it again.
"""

from datetime import datetime, timedelta

import database as db
from departments import department_name

# (days_ago, category, severity, severity_reason, summary, language, location,
#  status, action_taken, [feedback...])
SAMPLES = [
    (9, "roads", "High", "Large pothole on a busy main road is causing two-wheeler falls.",
     "Large pothole on the main road near the bus depot causing bike accidents.",
     "English", "Station Road, near KSRTC bus depot", "Resolved",
     "Pothole filled with hot-mix asphalt and the patch levelled on 14 Nov.",
     ["Fixed within a week, thank you!", "Road is smooth now."]),
    (4, "roads", "Medium", "Small pothole at the road edge, not on the main carriageway.",
     "Small pothole on the side of the lane outside the community hall.",
     "Kannada", "4th Cross, Jayanagar", "In Progress",
     "Site inspected. Patch work scheduled for this week.", []),
    (1, "roads", "Medium", "Broken footpath slabs are a tripping risk but not urgent.",
     "Several footpath slabs broken outside the vegetable market.",
     "Hindi", "Gandhi Bazaar", "Confirmed", "", []),

    (6, "waste", "High", "Garbage bin overflowing for days and spreading onto the road.",
     "Community garbage bin overflowing for 4 days, waste spilling onto the road.",
     "Hindi", "Market Street, Sector 7", "Resolved",
     "Bin cleared and collection frequency increased to daily.",
     ["Cleared, but please keep it daily."]),
    (2, "waste", "Low", "Small amount of litter, routine cleaning.",
     "Some litter and plastic wrappers near the park entrance.",
     "English", "Cubbon Park east gate", "Confirmed", "", []),

    (3, "water", "High", "Water leak wasting supply and getting worse.",
     "Water pipeline leaking at the junction, water flowing on the road since morning.",
     "Tamil", "Anna Nagar 2nd Avenue", "In Progress",
     "Leak located. Pipe replacement crew assigned.",
     ["Still leaking as of today evening."]),

    (5, "electricity", "Medium", "A single streetlight is out, no immediate danger.",
     "Streetlight not working on the residential lane for a week.",
     "Telugu", "Lane 3, Madhapur", "Resolved",
     "Faulty LED fitting replaced.", []),
    (0, "electricity", "Critical", "Exposed live wire near a school entrance.",
     "Electric wire hanging low from a pole near the school gate.",
     "English", "Govt. Primary School, Ward 12", "In Progress",
     "Area cordoned off. Power company informed for urgent repair.", []),

    (7, "trees", "Critical", "Fallen tree blocking a main road.",
     "Large tree fallen across the main road after last night's rain, traffic blocked.",
     "Marathi", "FC Road, near Fergusson College", "Resolved",
     "Tree cut and removed within 3 hours. Road reopened.",
     ["Very quick response, great work."]),
    (2, "trees", "Low", "Overgrown branches but not blocking anything.",
     "Overgrown branches touching the compound wall, needs pruning.",
     "English", "Koramangala 5th Block", "Confirmed", "", []),

    (3, "drainage", "Critical", "Open manhole on a busy road is a danger to life.",
     "Manhole cover missing on a busy road, very dangerous at night.",
     "Hinglish", "MG Road, opposite City Mall", "In Progress",
     "Temporary barricade placed. New cover ordered.", []),
    (8, "drainage", "High", "Sewage overflow affecting many houses.",
     "Sewage overflowing into the street from a blocked drain.",
     "Bengali", "Salt Lake, Sector 2", "Resolved",
     "Drain line de-silted with jetting machine.", []),

    (1, "other", "Low", "Not clearly a civic infrastructure issue.",
     "Request for a new bus shelter near the colony entrance.",
     "English", "", "Confirmed", "", []),

    # older, still-open complaints -> past their deadline (show up as overdue reminders)
    (12, "roads", "High", "Large pothole on a main road that is getting deeper with traffic.",
     "Deep pothole in the middle of 100 Feet Road, vehicles swerving dangerously.",
     "English", "100 Feet Road, Indiranagar", "Confirmed", "",
     ["Still not fixed after almost two weeks."]),
    (20, "waste", "Medium", "Uncollected waste, no immediate danger.",
     "Garbage not collected from the lane for several days, stray dogs scattering it.",
     "Kannada", "7th Main, BTM Layout", "In Progress",
     "Collection vehicle rerouted; waiting for the new route schedule.", []),
]


# Sample "Request urgent action" entries: SAMPLES index -> list of
# (reason, days, ai_verdict, ai_note, status, officer_note)
URGENCY_SAMPLES = {
    2: [("Elderly people walk here to the market every morning and two have already tripped.",
         3, "Weak", "Photo shows broken slabs but no clear sign of heavy elderly footfall.",
         "Pending review", "")],
    5: [("Water is flowing into the primary school compound next to the leak; children wade through it.",
         2, "Supported", "Photo shows water pooling at a school gate, matching the reason.",
         "Accepted", "Repair moved up; crew to finish within 2 days.")],
    9: [("Painters are coming to my house next week and the branches are in the way.",
          2, "Not supported", "Reason is personal convenience; branches block nothing public.",
          "Rejected", "Not a public-safety ground. Normal priority applies.")],
}


def main():
    db.init_db()
    now = datetime.now().replace(microsecond=0)
    for index, (days, cat, sev, reason, summary, lang, loc, status, action, feedback) in enumerate(SAMPLES):
        filed = now - timedelta(days=days, hours=3)
        ai = {
            "category": cat, "severity": sev, "severity_reason": reason,
            "department": department_name(cat), "summary": summary,
            "detected_language": lang, "location": loc,
            "english_translation": "", "image_observations": "", "ai_engine": "sample data",
        }
        code, _token = db.create_ticket(ai, summary, loc, [], created_at=filed.isoformat())
        db.confirm_ticket(code, at=(filed + timedelta(minutes=2)).isoformat())
        if status != "Confirmed":
            db.set_status_and_action(code, status, action, by="Demo Officer",
                                     at=min(filed + timedelta(hours=20), now).isoformat())
        row = db.get_ticket_row(code)
        for reason, days_wanted, verdict, note, ur_status, officer_note in URGENCY_SAMPLES.get(index, []):
            rid, _ = db.create_urgency_request(
                row, reason, days_wanted, [], {"verdict": verdict, "note": note, "ai_engine": "sample data"},
                from_complainant=False, at=min(filed + timedelta(hours=6), now).isoformat())
            if ur_status != "Pending review":
                db.decide_urgency_request(rid, ur_status, officer_note,
                                          at=min(filed + timedelta(hours=9), now).isoformat())
        for i, text in enumerate(feedback):
            db.add_feedback(row, text, from_complainant=(i == 0),
                            at=min(filed + timedelta(days=1, hours=i), now).isoformat())
    print(f"Added {len(SAMPLES)} sample complaints. Start the app and open /departments.")


if __name__ == "__main__":
    main()
