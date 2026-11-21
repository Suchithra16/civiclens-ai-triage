"""
CivicLens - AI triage engine.

Turns a raw citizen complaint (text in any Indian language and/or photos)
into a structured ticket with these fields:

    category, severity, severity_reason, department, summary,
    detected_language, location
    (+ extras for the officer: english_translation, image_observations)

Engines (pick with AI_PROVIDER in .env):
  - "gemini" : Google Gemini (free tier, good with Indian languages + photos)
  - "claude" : Anthropic Claude
  - "demo"   : offline keyword rules, no API key needed (cannot translate or see photos)
  - "auto"   : (default) gemini if GEMINI_API_KEY is set, else claude if
               ANTHROPIC_API_KEY is set, else demo

If a real AI call fails (bad key, no internet, rate limit...), we fall back to
demo mode so the citizen still gets a ticket.
"""

import base64
import json
import logging
import os
import re

log = logging.getLogger("civiclens.ai")

# ---------------------------------------------------------------------------
# Ticket vocabulary (shared by every engine)
# ---------------------------------------------------------------------------

# Departments live in departments.py (one per category). Routing is done in
# code, not by the AI, so it is always consistent.
from departments import CATEGORIES, department_name

SEVERITIES = ["Low", "Medium", "High", "Critical"]

SYSTEM_PROMPT = f"""You are the triage engine of CivicLens, a municipal complaint system in India.
A citizen sends a complaint: text in English or any Indian language (Hindi, Tamil,
Telugu, Kannada, Malayalam, Marathi, Bengali, Gujarati, Punjabi, Odia, Urdu, etc.,
possibly mixed with English or written in Latin script like "Hinglish"), and/or a
one or more photos, and an optional location field.

Turn it into ONE ticket for a municipal officer. Reply with ONLY a JSON object, no
markdown, with exactly these keys:

"category": exactly one of {json.dumps(CATEGORIES)}
    roads       = potholes, damaged roads/footpaths, speed breakers
    waste       = garbage, litter, dumping, dustbins, uncollected waste
    water       = drinking water supply, pipe leaks, no water, dirty water
    electricity = streetlights, hanging/live wires, poles, transformers
    trees       = fallen trees, dangerous or overgrown branches, tree cutting
    drainage    = blocked drains, sewage overflow, open/broken manholes, waterlogging
    other       = anything else

"severity": exactly one of {json.dumps(SEVERITIES)}, using these guidelines:
    Critical = emergency, immediate danger to life or safety. Examples: any danger
               near a school or hospital, open manhole on a busy road, tree fallen
               on a house or blocking a main road, live electric wire.
    High     = serious problem affecting many people or getting worse. Examples:
               water leak, large pothole on a main road, garbage overflowing.
    Medium   = real problem but not urgent. Examples: a single broken streetlight,
               a small pothole on the side of a road.
    Low      = minor problem or routine maintenance. Examples: small litter,
               overgrown branches that are not blocking anything.
    If torn between two levels and people could get hurt, choose the higher one.

"severity_reason": ONE short English sentence explaining why that severity.

"summary": ONE clear English line (max ~20 words) describing the issue, so an
           officer understands it without reading the original.

"detected_language": the language the citizen wrote in, e.g. "English", "Hindi",
                     "Tamil", "Hinglish". Use "None" if there was no text.

"location": the location mentioned by the citizen (in the location field or in the
            complaint text), written in English/Latin script. Combine both if they
            add different details. Use "" (empty string) if no location is mentioned.
            Never guess a location.

"english_translation": faithful English translation of the citizen's text.
                       "" if the text is already English or there is no text.

"image_observations": one or two sentences on what the photo(s) show that matters
                      for this issue. "" if there are no photos.

Rules: use the photos to judge category and severity too. If the photos and text
disagree, trust what you can see and mention it in the summary. Never invent facts
that are not in the text or photo. If it is not a civic issue, use category "other"
and severity "Low".
"""

# --- "Request urgent action" proof check -------------------------------------

URGENCY_VERDICTS = ["Supported", "Weak", "Not supported", "Unverified"]

URGENCY_PROMPT = """You check "urgent action" requests for CivicLens, a municipal complaint
system in India. A citizen wants an already-registered complaint fixed faster than its
normal priority. They gave a reason (English or any Indian language), the number of days
they want it done in, and proof photos (the situation itself, or a document such as a
school or hospital letter).

Decide whether the PROOF genuinely supports the REASON. Reply with ONLY a JSON object,
no markdown, with exactly these keys:

"verdict": one of "Supported", "Weak", "Not supported"
    Supported     = the photos clearly show what the reason claims, AND the reason is a real
                    public-interest ground for urgency: risk to life or safety; children,
                    elderly people or patients affected; a school, hospital or other essential
                    service affected; damage getting worse quickly; many people affected.
    Weak          = the proof is related but does not clearly prove the claim, or the reason
                    is only partly a public-interest ground.
    Not supported = the proof is unrelated, unclear or does not match the complaint, or the
                    reason is only personal convenience.
"note": ONE short English sentence for the municipal officer explaining the verdict.

Be strict: a request without convincing proof must not jump the queue. Judge only what
you can see and read; never assume facts that are not shown.
"""


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def triage(text, images=None, location=None):
    """Turn a complaint into a validated ticket dict. Never raises.

    images: list of (image_bytes, mime_type) tuples - zero, one or several photos.
    """
    text = (text or "").strip()
    location = (location or "").strip()
    images = images or []
    engine = _pick_engine()

    try:
        if engine == "demo":
            raw = _demo_triage(text, images, location)
        else:
            raw = _ask(engine, SYSTEM_PROMPT, _user_prompt(text, location, len(images)), images)
        ticket = _validate(raw, location)
        ticket["ai_engine"] = engine
    except Exception as exc:  # any API / parsing failure -> still give a ticket
        log.exception("AI engine '%s' failed, using demo fallback", engine)
        ticket = _validate(_demo_triage(text, images, location), location)
        ticket["ai_engine"] = f"demo (fallback: {engine} failed - {type(exc).__name__})"

    return ticket


def check_urgency(complaint, reason, days, proof_images):
    """AI proof check for an "urgent action" request. Advisory only - an officer decides.

    complaint: dict with summary, category, severity, location
    Returns {"verdict": one of URGENCY_VERDICTS, "note": str, "ai_engine": str}. Never raises.
    """
    engine = _pick_engine()
    if engine == "demo":
        return _demo_urgency_check()

    prompt = (
        "REGISTERED COMPLAINT:\n"
        f"  summary: {complaint.get('summary', '')}\n"
        f"  category: {complaint.get('category', '')}\n"
        f"  current severity: {complaint.get('severity', '')}\n"
        f"  location: {complaint.get('location') or 'not given'}\n\n"
        f"CITIZEN'S REASON FOR URGENCY:\n{reason}\n\n"
        f"WANTS IT DONE WITHIN: {days} day(s)\n\n"
        f"PROOF PHOTOS ATTACHED: {len(proof_images)}"
    )
    try:
        raw = _ask(engine, URGENCY_PROMPT, prompt, proof_images)
        verdict = _clean(raw.get("verdict"), 30)
        if verdict not in URGENCY_VERDICTS[:3]:
            verdict = "Unverified"
        note = _clean(raw.get("note"), 300) or "No explanation given by the AI."
        return {"verdict": verdict, "note": note, "ai_engine": engine}
    except Exception as exc:
        log.exception("AI urgency check with '%s' failed", engine)
        result = _demo_urgency_check()
        result["ai_engine"] = f"none ({engine} failed - {type(exc).__name__})"
        return result


# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------

def _pick_engine():
    choice = os.getenv("AI_PROVIDER", "auto").strip().lower()
    if choice in ("gemini", "claude", "demo"):
        return choice
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    return "demo"


def _user_prompt(text, location, photo_count):
    return (
        "COMPLAINT TEXT:\n"
        + (text if text else "(no text - photo only)")
        + "\n\nLOCATION FIELD:\n"
        + (location if location else "(not provided)")
        + "\n\nPHOTOS ATTACHED: "
        + str(photo_count)
    )


def _ask(engine, system_prompt, user_text, images):
    """Send instructions + text + photos to the chosen AI, return its JSON reply as a dict."""
    if engine == "gemini":
        return _ask_gemini(system_prompt, user_text, images)
    if engine == "claude":
        return _ask_claude(system_prompt, user_text, images)
    raise ValueError(f"Unknown AI engine: {engine}")


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def _ask_gemini(system_prompt, user_text, images):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    contents = [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in images]
    contents.append(user_text)

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    return _parse_json(response.text)


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _ask_claude(system_prompt, user_text, images):
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.b64encode(data).decode("ascii"),
            },
        }
        for data, mime in images
    ]
    content.append({"type": "text", "text": user_text})

    message = client.messages.create(
        model=model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )
    reply = "".join(block.text for block in message.content if block.type == "text")
    return _parse_json(reply)


def _parse_json(reply):
    """Pull the JSON object out of a model reply (tolerates ```json fences)."""
    reply = (reply or "").strip()
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        raise ValueError("AI reply contained no JSON object")
    return json.loads(match.group(0))


# ---------------------------------------------------------------------------
# Validation - never trust model output blindly
# ---------------------------------------------------------------------------

def _clean(value, max_len):
    if value is None:
        return ""
    return " ".join(str(value).split())[:max_len]  # also collapses newlines


def _validate(raw, location_field):
    category = _clean(raw.get("category"), 30).lower()
    if category not in CATEGORIES:
        category = "other"

    severity = _clean(raw.get("severity"), 20).capitalize()
    if severity not in SEVERITIES:
        severity = "Medium"

    location = _clean(raw.get("location"), 300)
    if not location and location_field:  # citizen typed a location -> never lose it
        location = _clean(location_field, 300)

    return {
        "category": category,
        "severity": severity,
        "severity_reason": _clean(raw.get("severity_reason"), 300),
        "department": department_name(category),
        "summary": _clean(raw.get("summary"), 250) or "Citizen complaint (see details)",
        "detected_language": _clean(raw.get("detected_language"), 40) or "Unknown",
        "location": location,
        # extras for the officer
        "english_translation": _clean(raw.get("english_translation"), 3000),
        "image_observations": _clean(raw.get("image_observations"), 600),
    }


# ---------------------------------------------------------------------------
# Demo engine (offline, rule-based) - lets the app run without any API key
# ---------------------------------------------------------------------------

_SCRIPTS = [
    ("ऀ", "ॿ", "Hindi/Marathi"),
    ("ঀ", "৿", "Bengali/Assamese"),
    ("਀", "੿", "Punjabi"),
    ("઀", "૿", "Gujarati"),
    ("଀", "୿", "Odia"),
    ("஀", "௿", "Tamil"),
    ("ఀ", "౿", "Telugu"),
    ("ಀ", "೿", "Kannada"),
    ("ഀ", "ൿ", "Malayalam"),
    ("؀", "ۿ", "Urdu"),
]

_KEYWORDS = {
    "roads": ["pothole", "road", "footpath", "speed breaker", "gaddha", "sadak",
              "सड़क", "गड्ढा", "சாலை", "రోడ్డు", "ರಸ್ತೆ"],
    "waste": ["garbage", "trash", "waste", "dustbin", "litter", "kachra", "dump",
              "कचरा", "कूड़ा", "குப்பை", "చెత్త", "ಕಸ"],
    "water": ["water supply", "no water", "tap", "pipeline", "pipe", "leak", "paani",
              "पानी", "தண்ணீர்", "నీరు", "ನೀರು"],
    "electricity": ["streetlight", "street light", "light pole", "electric", "wire",
                    "transformer", "bijli", "बिजली", "लाइट", "மின்"],
    "trees": ["tree", "branch", "पेड़", "மரம்", "చెట్టు", "ಮರ"],
    "drainage": ["drain", "sewage", "sewer", "gutter", "manhole", "nala", "waterlogg",
                 "नाली", "सीवर", "கழிவுநீர்", "మురుగు"],
}

# (severity, reason, trigger phrases) - checked top to bottom
_SEVERITY_RULES = [
    ("Critical", "Mentions an immediate danger to life or safety.",
     ["live wire", "electrocut", "spark", "open manhole", "fallen on", "fell on",
      "blocking main road", "blocking the main road", "collapse", "fire", "accident",
      "करंट", "आग"]),
    ("High", "Serious problem that affects many people or is getting worse.",
     ["leak", "overflow", "large pothole", "big pothole", "main road", "no water",
      "days", "sewage", "spreading", "बह रहा", "ओवरफ्लो"]),
    ("Low", "Minor issue or routine maintenance.",
     ["small litter", "litter", "overgrown", "paint", "minor", "maintenance"]),
]
_DANGER_WORDS = ["danger", "hazard", "risk", "wire", "manhole", "pothole", "fallen", "खतरा"]
_SENSITIVE_PLACES = ["school", "hospital", "स्कूल", "अस्पताल"]

_LOCATION_PATTERN = re.compile(
    r"\b(?:near|opposite|opp\.?|behind|in front of|outside|at|on|in)\s+"
    r"([A-Z0-9][^,.;!?\n]{2,60})"
)
# words that end a location phrase: "Market Street for 3 days" -> "Market Street"
_LOCATION_STOP = re.compile(r"\s+(?:for|since|from|and|but|with|is|are|has|have|which|where|people)\b.*$", re.I)

# generic words count less than specific ones ("open manhole on MG Road" is drainage)
_WEAK_KEYWORDS = {"road", "sadak", "pipe", "wire", "tap"}


def _detect_language(text):
    if not text:
        return "None"
    for start, end, name in _SCRIPTS:
        if any(start <= ch <= end for ch in text):
            return name
    return "English"


def _demo_severity(lower):
    if any(p in lower for p in _SENSITIVE_PLACES) and any(d in lower for d in _DANGER_WORDS):
        return "Critical", "Possible danger near a school or hospital."
    for level, reason, words in _SEVERITY_RULES:
        if any(w in lower for w in words):
            return level, reason
    return "Medium", "Real problem but no sign of immediate danger."


def _demo_triage(text, images, location):
    lower = text.lower()

    scores = {
        cat: sum((1 if kw in _WEAK_KEYWORDS else 3) for kw in kws if kw in lower)
        for cat, kws in _KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    category = best if scores[best] > 0 else "other"

    severity, reason = _demo_severity(lower)
    if category == "other" and severity == "Medium":
        severity, reason = "Low", "Not clearly a civic issue."
    if not text:
        severity, reason = "Medium", "Photo-only complaint; officer should verify severity."

    if text:
        first_line = text.splitlines()[0]
        summary = first_line if len(first_line) <= 120 else first_line[:117] + "..."
    else:
        summary = "Photo-only complaint - see attached photos."

    found = _LOCATION_PATTERN.search(text)
    text_location = _LOCATION_STOP.sub("", found.group(1)).strip() if found else ""
    full_location = ", ".join(x for x in (location, text_location) if x)

    language = _detect_language(text)
    return {
        "category": category,
        "severity": severity,
        "severity_reason": reason + " (demo rules)",
        "summary": summary,
        "detected_language": language,
        "location": full_location,
        "english_translation": "" if language in ("English", "None")
        else "(Translation needs a real AI engine - add an API key in .env)",
        "image_observations": "(Photo analysis needs a real AI engine - add an API key in .env)"
        if images else "",
    }


def _demo_urgency_check():
    return {
        "verdict": "Unverified",
        "note": "Proof check needs a real AI key - an officer will review the proof manually.",
        "ai_engine": "demo",
    }
