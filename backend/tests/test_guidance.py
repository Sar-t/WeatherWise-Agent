"""When to retrieve guidelines, what to ask for, and the safety rules of the GUIDANCE section."""

from pathlib import Path

import pytest

from backend.agent.guidance import (
    FIRST_AID_CHARS,
    GUIDANCE_CHARS,
    GuidanceRequest,
    guidance_line,
    plan_guidance,
    select_groups,
    summarize_guidance,
)
from backend.agent.schemas import QueryUnderstanding
from backend.services.guidelines import Guideline
from backend.services.models import WeatherAlert
from backend.services.scoring import assess_activity
from backend.tests.test_observations import air_result
from backend.tests.test_scoring import NOW, air, at, with_hours
from ingestion.build_index import DOCS_DIR, chunk_document, parse_document


def guideline(doc_id: str) -> Guideline:
    """A retrieved guideline built from the real document (as the index would return it)."""
    doc = parse_document(DOCS_DIR / f"{doc_id}.md")
    chunk = chunk_document(doc)[0]
    return Guideline(doc_id=doc.id, title=doc.title, text=chunk.text, source_name=doc.source_name,
                     source_url=doc.source_url, topics=list(doc.topics), score=0.9,
                     severity=" | ".join(doc.severity))


def u(**slots) -> QueryUnderstanding:
    return QueryUnderstanding(**({"intent": "weather", "locations": ["Delhi"]} | slots))


def pleasant() -> list:
    return [assess_activity("running", [with_hours(at(6), 2, {})], now=NOW)]


def hot() -> list:
    hot_hour = {"feels_like_c": 34}  # every hour, so the best block is hot too
    return [assess_activity("running", [with_hours(at(6), 2, {0: hot_hour, 1: hot_hour})], now=NOW)]


def alert(event: str) -> WeatherAlert:
    return WeatherAlert(sender="IMD", event=event, start=at(6), end=at(9), description="")


# --- triggers ----------------------------------------------------------------------------


def test_no_reason_no_retrieval() -> None:
    assert plan_guidance(u(), [], [], []) is None
    assert plan_guidance(u(intent="activity_recommendation"), pleasant(), [], []) is None
    assert plan_guidance(u(), [], [air(at(6), 3, naqi=150)], []) is None  # < 201 and not asked about


def test_assessment_topics_trigger() -> None:
    req = plan_guidance(u(intent="activity_recommendation"), hot(), [], [])
    assert (req.topics, req.activity, req.audience, req.reasons) == (["heat"], "running", "general",
                                                                     ["assessment topics"])


def test_poor_air_in_india_triggers_with_naqi_severity() -> None:
    req = plan_guidance(u(), [], [air_result()], [])  # worst NAQI 230, Poor
    assert req.topics == ["air_quality"] and req.severity == "NAQI Poor"
    assert "NAQI Poor or worse" in req.reasons
    abroad = air_result("fr")  # OpenWeather scale only, no NAQI
    assert plan_guidance(u(), [], [abroad], []) is None


def test_severe_alert_triggers_with_alert_severity() -> None:
    req = plan_guidance(u(), [], [], [alert("Heat Wave")])
    assert (req.topics, req.severity, req.reasons) == (["heat"], "heat wave", ["severe alert"])
    storm = plan_guidance(u(), [], [], [alert("Thunderstorm & Lightning")])
    assert storm.topics == ["thunderstorm"] and storm.severity == "thunderstorm"
    assert plan_guidance(u(), [], [], [alert("Moderate showers")]) is None  # not severe


def test_precautions_trigger_and_nothing_specific_to_look_up() -> None:
    req = plan_guidance(u(intent="activity_recommendation", asks_precautions=True), hot(), [], [])
    assert "asks precautions" in req.reasons
    # "is it safe?" on a perfect morning: a reason, but nothing to look up -> no retrieval
    assert plan_guidance(u(intent="activity_recommendation", asks_precautions=True), pleasant(), [], []) is None


def test_precautions_about_air_add_air_quality_above_satisfactory() -> None:
    moderate = air(at(6), 3, naqi=110, changes={0: 126})
    req = plan_guidance(u(intent="air_quality", include_air_quality=True, asks_precautions=True,
                          audience="children"), [], [moderate], [])
    assert req.topics == ["air_quality"] and req.severity == "NAQI Moderately Polluted"
    assert req.audience == "children"


def test_audience_needs_less_than_ideal_conditions() -> None:
    assert plan_guidance(u(audience="older_adults"), pleasant(), [], []) is None
    req = plan_guidance(u(intent="activity_recommendation", audience="older_adults"), hot(), [], [])
    assert req.audience == "older_adults" and "audience in less-than-ideal conditions" in req.reasons


def test_pregnant_maps_to_sensitive_groups() -> None:
    req = plan_guidance(u(intent="activity_recommendation", audience="pregnant"), hot(), [], [])
    assert req.audience == "sensitive_groups"


def test_poor_air_and_severe_alerts_count_whatever_the_blocks() -> None:
    # The best block is perfect, so it adds no topics; Poor air and a severe alert still do.
    req = plan_guidance(u(intent="activity_recommendation", recommendation_type="suitability"), pleasant(),
                        [air_result()], [alert("Heat Wave")])
    assert req.topics == ["heat", "air_quality"] and req.severity == "NAQI Poor; heat wave"
    assert set(req.reasons) == {"NAQI Poor or worse", "severe alert"}


def test_combined_severity_and_request_shape() -> None:
    req = plan_guidance(u(), [], [air_result()], [alert("Severe Heatwave")])
    assert req.topics == ["heat", "air_quality"] and req.severity == "NAQI Poor; heat wave"
    assert req.kwargs() == {"topics": ["heat", "air_quality"], "severity": "NAQI Poor; heat wave",
                            "audience": "general", "activity": None, "k": 3}


# --- GUIDANCE section: never split qualifiers, first aid whole ---------------------------------


HYPOTHERMIA_DRINKS = ("If they are fully alert, give warm drinks, never alcohol. "
                      "Give no fluids if they are not fully alert.")
MASK_ONLY_SHORT = ("If you use a mask, a disposable N95 or N99 can help, but only for short exposures and only "
                   "if it fits closely over the nose and mouth.")


@pytest.mark.parametrize("words", [
    {"cold", "warm", "drinks"}, {"alcohol"}, {"fluids", "alert"}, {"shivering"}, {"frostbite", "massage"},
    {"cold", "wave", "older", "elderly"},
])
def test_hypothermia_drinks_pairing_is_never_split(words: set[str]) -> None:
    text = " ".join(select_groups(guideline("cold-frostbite-hypothermia").text, words, 150))
    if "warm drinks" in text or "no fluids" in text:
        assert HYPOTHERMIA_DRINKS in text


@pytest.mark.parametrize("words", [
    {"mask", "n95"}, {"short", "exposures"}, {"air", "pollution", "mask"}, {"asthma", "heart", "lung"},
])
def test_mask_short_exposure_qualifier_is_never_split(words: set[str]) -> None:
    text = " ".join(select_groups(guideline("air-pollution-masks-and-vulnerable-groups").text, words, 150))
    if "N95" in text:
        assert MASK_ONLY_SHORT in text


def test_a_lead_in_keeps_its_list() -> None:
    groups = select_groups(guideline("air-pollution-masks-and-vulnerable-groups").text, {"medicines"}, 0)
    assert groups[0].startswith("On Poor to Severe days (AQI above 200): Take extra care")
    assert "Get medical advice straight away if they do." in groups[0]


def test_a_qualifying_sentence_stays_with_the_one_before(tmp_path: Path) -> None:
    text = "Title\n\n## Water\n\nDrink water often. Never wait until you are thirsty.\n\nRest in the shade."
    assert select_groups(text, {"drink", "water"}, 0) == ["Drink water often. Never wait until you are thirsty."]


def test_first_aid_sections_go_in_whole() -> None:
    doc = guideline("cold-frostbite-hypothermia")
    text = " ".join(select_groups(doc.text, {"hypothermia"}, 150))
    for step in ("Move them somewhere warm", "skin-to-skin", HYPOTHERMIA_DRINKS, "Seek medical help if they get worse."):
        assert step in text
    assert len(text) <= FIRST_AID_CHARS + 100
    lightning = " ".join(select_groups(guideline("thunderstorm-lightning-indoors-first-aid").text,
                                       {"lightning", "struck"}, 150))
    assert "Take them to a hospital" in lightning and "safe to touch" in lightning and "burns" in lightning


def test_guidance_section_size_and_shape() -> None:
    docs = [guideline("heat-stay-cool"), guideline("heat-exertion-and-vulnerable-groups")]
    req = GuidanceRequest(["heat"], None, "general", "walking")
    text = summarize_guidance(docs, req.words())
    lines = text.splitlines()
    assert lines[0].startswith("GUIDANCE (official sources")
    assert lines[1].startswith("- Hot weather and heatwaves: staying cool and hydrated (WHO")
    assert len(text) <= GUIDANCE_CHARS + 400  # titles and sources on top of ~600 characters of passages


def test_naqi_band_documents_for_other_bands_are_dropped() -> None:
    from backend.agent.guidance import keep_relevant

    docs = [guideline(d) for d in ("naqi-moderately-polluted", "naqi-good-satisfactory", "exercise-in-polluted-air",
                                   "particulate-matter-basics")]
    kept = [d.doc_id for d in keep_relevant(docs, "NAQI Moderately Polluted")]
    assert kept == ["naqi-moderately-polluted", "particulate-matter-basics"]  # untagged docs stay
    assert [d.doc_id for d in keep_relevant(docs, "heat wave")] == [d.doc_id for d in docs]  # no NAQI asked
    assert [d.doc_id for d in keep_relevant(docs, "NAQI Poor; heat wave")] == ["exercise-in-polluted-air",
                                                                               "particulate-matter-basics"]


def test_template_guidance_line_is_a_whole_group_with_source() -> None:
    req = GuidanceRequest(["air_quality"], "NAQI Poor", "sensitive_groups", "running")
    line = guidance_line([guideline("air-pollution-masks-and-vulnerable-groups")], req.words())
    assert line.startswith("Guidance (MoHFW")
    assert line.endswith("Get medical advice straight away if they do.")
    assert guidance_line([], req.words()) is None
