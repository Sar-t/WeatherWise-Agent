"""Text checks from the answer evals (evals/run_answers.py); no LLM."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from evals.run_answers import check_best_time, check_internal_terms

IST = timezone(timedelta(hours=5, minutes=30))


def assessment(*blocks: tuple[str, str]) -> SimpleNamespace:
    day = "2026-10-26"
    return SimpleNamespace(best_blocks=[
        SimpleNamespace(start=datetime.fromisoformat(f"{day}T{s}:00").replace(tzinfo=IST),
                        end=datetime.fromisoformat(f"{day}T{e}:00").replace(tzinfo=IST)) for s, e in blocks])


# A real reply from the live API on 25 Oct 2026 ("Can I go for a run in Delhi tomorrow morning?"): the
# engine's best blocks were 06:30-07:30 and 07:30-08:30, and the model merged them into one window.
CAPTURED = ("Tomorrow morning is a good time for a run in Delhi as conditions are rated suitable with a perfect "
            "score. The best window for your activity is between 06:30 and 08:30 when temperatures are likely to "
            "be between 20°C and 22°C. The sky is expected to be clear with a 0% chance of rain and light winds "
            "reaching only 8 km/h by midday.")


def test_a_widened_best_time_fails() -> None:
    [failure] = check_best_time(CAPTURED, [assessment(("06:30", "07:30"), ("07:30", "08:30"))])
    assert failure == "best time 06:30-08:30 doesn't match the assessment's best window 06:30-07:30"


def test_the_exact_best_window_passes_in_any_wording() -> None:
    a = [assessment(("20:30", "21:30"))]
    for reply in ("The best time for your run is between 20:30 and 21:30 when the air improves.",
                  "Best: 20:30-21:30.",
                  "The ideal window is 8:30 pm to 9:30 pm.",
                  "Sabse accha samay 20:30 se 21:30 tak hai."):
        assert check_best_time(reply, a) == [], reply


def test_ranges_outside_best_time_wording_are_not_checked() -> None:
    a = [assessment(("06:30", "07:30"))]
    reply = ("It's a good morning for a run. Temperatures rise from 06:00 to 12:00. The best time is between 06:30 "
             "and 07:30, but avoid 12:00 to 15:00 when it's hottest.")
    assert check_best_time(reply, a) == []


def test_a_narrowed_window_fails_and_comparisons_check_each_place() -> None:
    assert check_best_time("Best between 06:30 and 07:00.", [assessment(("06:30", "07:30"))])
    two = [assessment(("06:30", "07:30")), assessment(("07:00", "08:00"))]
    assert check_best_time("Delhi is best 06:30-07:30 and Mumbai is best 07:00-08:00.", two) == []


def test_nothing_to_check_without_a_best_block() -> None:
    assert check_best_time("Best between 06:30 and 08:30.", [assessment()]) == []


# --- internal terms --------------------------------------------------------------------------------

def test_internal_terms_fail_in_real_replies() -> None:
    # Sentences from real cached eval replies (aq_run_delhi_morning, activity_suitability_run).
    assert check_internal_terms(
        "The scoring engine gives this time a perfect score because no weather factors are expected to hinder "
        "your activity.") == ["mentions internal term 'scoring engine'", "mentions internal term 'score'"]
    assert check_internal_terms("The activity assessment verdict is not suitable.") == [
        "mentions internal term 'assessment'"]
    assert check_internal_terms("Based on the observations and data provided, it stays dry.") == [
        "mentions internal term 'observations'", "mentions internal term 'data provided'"]


def test_plain_verdict_wording_passes() -> None:
    assert check_internal_terms("Conditions look ideal for a run in Delhi tomorrow between 06:30 and 07:30. "
                                "Scorching heat is not expected.") == []
