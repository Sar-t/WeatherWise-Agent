"""Official guidance for a turn: when to retrieve it, what to ask for, and what to show the model.

Retrieval only happens for a reason (an assessment lost points, Poor+ air in India, a severe alert,
a precautions question, or a named audience in less-than-ideal conditions), so most replies stay
short. The request is built from structured data (topics, NAQI category, alerts, audience, profile),
never from the raw message. The GUIDANCE section keeps whole sentences/bullets, never separates a
condition or prohibition from what it qualifies, and includes first-aid sections whole.
"""

import re
from dataclasses import dataclass, field

from backend.agent.assess import ALERT_TOPICS, guideline_topics
from backend.agent.schemas import QueryUnderstanding, wants_air_quality
from backend.services.guidelines import severity_match
from backend.services.models import AirQualityResult, WeatherAlert
from backend.services.scoring.config import load_config
from backend.services.scoring.models import ActivityAssessment

POOR_NAQI = 201
SATISFACTORY_NAQI_MAX = 100
RETRIEVE_K = 3
MAX_DOCS = 2  # given to the answer model
GUIDANCE_CHARS = 600  # GUIDANCE section budget
FIRST_AID_CHARS = 1000  # hard cap when a first-aid section is included whole
# Audiences the documents are tagged with; "pregnant" is covered by sensitive_groups (MoHFW lists
# pregnant women among the groups vulnerable to air pollution).
AUDIENCE_TAGS = {"pregnant": "sensitive_groups"}
ALERT_SEVERITY = {"heat": "heat wave", "cold": "cold wave", "thunderstorm": "thunderstorm", "rain": "heavy rain",
                  "wind": "squall"}  # an alert's topic -> the severity tag documents use
TOPIC_WORDS = {  # extra words that tell which sentences of a document matter for a topic
    "air_quality": "air pollution aqi naqi mask outdoor exertion", "heat": "heat hot water shade sun",
    "cold": "cold warm layers clothing shivering", "thunderstorm": "thunder lightning shelter storm",
    "uv": "uv sun sunscreen shade hat", "rain": "rain flood water", "wind": "wind gusts",
}
AUDIENCE_WORDS = {
    "children": "children child kids infants pram", "older_adults": "older elderly 65 parents",
    "sensitive_groups": "asthma heart lung chronic disease conditions pregnant medicines",
    "outdoor_workers": "outdoor workers work", "general": "",
}
QUALIFIERS = re.compile(r"\b(only|unless|if|never|don't|do not|avoid|not)\b", re.I)
FIRST_AID_HEADINGS = re.compile(r"first aid|hypothermia|frostbite|struck by lightning|heat ?stroke|heat illness",
                                re.I)


@dataclass
class GuidanceRequest:
    """What to ask retrieve_guidelines() for, and why."""

    topics: list[str]
    severity: str | None
    audience: str
    activity: str | None
    reasons: list[str] = field(default_factory=list)  # which triggers fired

    def kwargs(self) -> dict:
        return {"topics": self.topics, "severity": self.severity, "audience": self.audience,
                "activity": self.activity, "k": RETRIEVE_K}

    def words(self) -> set[str]:
        text = " ".join([*self.topics, *(TOPIC_WORDS.get(t, "") for t in self.topics), self.severity or "",
                         AUDIENCE_WORDS.get(self.audience, ""), (self.activity or "").replace("_", " ")])
        return _words(text)


def plan_guidance(
    u: QueryUnderstanding,
    assessments: list[ActivityAssessment],
    air_cards: list[AirQualityResult],
    alerts: list[WeatherAlert],
) -> GuidanceRequest | None:
    """The retrieval request for this turn, or None when there's no reason to look anything up."""
    keywords = [k.lower() for k in load_config().severe_alert_keywords]
    severe = [a for a in alerts if any(k in a.event.lower() for k in keywords)]
    assessed = guideline_topics(assessments, [], u.recommendation_type)
    naqi, category = _worst_india_naqi(air_cards)

    reasons = []
    if assessed:
        reasons.append("assessment topics")
    if naqi is not None and naqi >= POOR_NAQI:
        reasons.append("NAQI Poor or worse")
    if severe:
        reasons.append("severe alert")
    if u.asks_precautions:
        reasons.append("asks precautions")
    not_ideal = bool(assessed) or bool(alerts) or (naqi is not None and naqi > SATISFACTORY_NAQI_MAX)
    if u.audience and not_ideal:
        reasons.append("audience in less-than-ideal conditions")
    if not reasons:
        return None

    topics = set(guideline_topics(assessments, alerts, u.recommendation_type))
    if naqi is not None and (naqi >= POOR_NAQI or (
            naqi > SATISFACTORY_NAQI_MAX and wants_air_quality(u) and (u.asks_precautions or u.audience))):
        topics.add("air_quality")
    alert_topics = {ALERT_TOPICS.get(k) for a in severe for k in keywords if k in a.event.lower()} - {None}
    severity_parts = ([f"NAQI {category}"] if "air_quality" in topics and category else []) + [
        ALERT_SEVERITY[t] for t in sorted(alert_topics) if t in ALERT_SEVERITY]
    if not topics and not severity_parts:
        return None  # e.g. "is it safe?" on a perfect morning: nothing specific to look up
    order = ["heat", "cold", "rain", "thunderstorm", "air_quality", "uv", "wind"]
    return GuidanceRequest(
        topics=[t for t in order if t in topics],
        severity="; ".join(severity_parts) or None,
        audience=AUDIENCE_TAGS.get(u.audience or "", u.audience or "general"),
        activity=assessments[0].profile if assessments else None,
        reasons=reasons,
    )


def keep_relevant(docs: list, severity: str | None) -> list:
    """Drop NAQI band documents for a different band than the request's ("NAQI Good" guidance on a
    Moderately Polluted day contradicts the data). Documents without NAQI tags are kept."""
    if not severity or "naqi" not in severity.lower():
        return docs
    return [d for d in docs if "naqi" not in d.severity.lower() or severity_match(severity, d.severity) > 0]


def _worst_india_naqi(air_cards: list[AirQualityResult]) -> tuple[int | None, str | None]:
    rated = [p for c in air_cards if c.location.country_code == "in" for p in c.points if p.naqi is not None]
    if not rated:
        return None, None
    worst = max(rated, key=lambda p: p.naqi)
    return worst.naqi, worst.naqi_category


# --- the GUIDANCE section ---------------------------------------------------------------------


@dataclass
class _Section:
    heading: str
    groups: list[list[str]]  # units (whole bullets/sentences) kept together

    @property
    def first_aid(self) -> bool:
        return bool(FIRST_AID_HEADINGS.search(self.heading))


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _clean(unit: str) -> str:
    """Drop markdown bold; end list items with ';' so joined items stay readable."""
    unit = unit.replace("**", "").strip()
    return unit if unit[-1:] in ".!?:;" else unit + ";"


def _group_lines(lines: list[str]) -> list[list[str]]:
    """Groups of whole units. A bullet (with its wrapped lines) is one unit and one group. A paragraph
    is split into sentences, and a sentence with a condition or prohibition ("only", "if", "never",
    "don't", "avoid", ...) stays in the group of the sentence before it, the one it qualifies."""
    groups: list[list[str]] = []
    bullet: str | None = None
    paragraph: list[str] = []
    lead: list[str] | None = None  # a group ending in ":" that the following list belongs to

    def flush() -> None:
        nonlocal bullet, lead
        if bullet is not None:
            if lead is not None:
                lead.append(_clean(bullet))  # "On Poor to Severe days:" + its bullets stay together
            else:
                groups.append([_clean(bullet)])
            bullet = None
        if paragraph:
            lead = None
            sentences = [s for s in re.split(r"(?<=[.!?:])\s+", " ".join(paragraph)) if s]
            current: list[str] = []
            for sentence in sentences:
                if current and not QUALIFIERS.search(sentence) and not current[-1].endswith(":"):
                    groups.append(current)
                    current = []
                current.append(_clean(sentence))
            groups.append(current)
            if current[-1].endswith(":"):
                lead = current
            paragraph.clear()

    for raw in lines:
        line = raw.strip()
        if line.startswith(("- ", "* ")):
            flush()
            bullet = line[2:].strip()
        elif not line:
            flush()
        elif bullet is not None:
            bullet += " " + line
        else:
            if lead is not None and bullet is None and not paragraph:
                lead = None  # a new paragraph ends the list
            paragraph.append(line)
    flush()
    return [g for g in groups if g]


def _sections(text: str) -> list[_Section]:
    """Split a retrieved chunk ("title line", then '## ' sections) into sections of unit groups."""
    lines = text.splitlines()[1:]  # the first line is the title (shown separately)
    sections, heading, body = [], "", []
    for line in lines:
        if line.startswith("## "):
            if body:
                sections.append(_Section(heading, _group_lines(body)))
            heading, body = line[3:].strip(), []
        else:
            body.append(line)
    if body:
        sections.append(_Section(heading, _group_lines(body)))
    return [s for s in sections if s.groups]


def select_groups(text: str, words: set[str], budget: int) -> list[str]:
    """The most relevant unit groups of a guideline, in document order, within `budget` characters.

    Groups are never split. Only groups that share words with the request are added (the best one
    always is). A relevant first-aid section goes in whole, raising the limit to FIRST_AID_CHARS,
    since partial first-aid steps can mislead.
    """
    sections = _sections(text)
    scored = sorted(((len(words & _words(" ".join(g) + " " + s.heading)), si, gi)
                     for si, s in enumerate(sections) for gi, g in enumerate(s.groups)),
                    key=lambda x: (-x[0], x[1], x[2]))
    chosen: set[tuple[int, int]] = set()
    limit = budget
    for score, si, gi in scored:
        if (si, gi) in chosen or (chosen and score == 0):
            continue
        if sections[si].first_aid:
            add, new_limit = {(si, j) for j in range(len(sections[si].groups))}, max(limit, FIRST_AID_CHARS)
        else:
            add, new_limit = {(si, gi)}, limit
        candidate = chosen | add
        if chosen and len(" ".join(_texts(sections, candidate))) > new_limit:
            continue
        chosen, limit = candidate, new_limit
    return _texts(sections, chosen)


def _texts(sections: list[_Section], chosen: set[tuple[int, int]]) -> list[str]:
    return [" ".join(sections[si].groups[gi]) for si, gi in sorted(chosen)]


def summarize_guidance(guidelines: list, words: set[str]) -> str:
    """The GUIDANCE section: per document, its title and source, then the selected passages."""
    per_doc = max(GUIDANCE_CHARS // max(len(guidelines), 1), 150)
    lines = ["GUIDANCE (official sources; the only safety advice you may give):"]
    for g in guidelines:
        lines.append(f"- {g.title} ({g.source_name}): {' '.join(select_groups(g.text, words, per_doc))}")
    return "\n".join(lines)


def guidance_line(guidelines: list, words: set[str]) -> str | None:
    """One line for the template fallback: the top document's most relevant group, whole."""
    if not guidelines:
        return None
    g = guidelines[0]
    groups = select_groups(g.text, words, 0)  # budget 0: just the best group (or a first-aid section)
    return f"Guidance ({g.source_name}): {groups[0]}" if groups else None
