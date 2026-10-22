"""Best-time and avoid blocks from scored slots. Pure functions.

Slots come grouped per requested window; a block never spans two windows or a gap.
"""

from statistics import fmean

from backend.services.scoring.factors import MARGINAL, verdict
from backend.services.scoring.models import FactorScore, HourAssessment, TimeBlock

MAX_BEST_BLOCKS = 2
BEST_REASONS = 3
AVOID_REASONS = 2


def runs(slots: list[HourAssessment]) -> list[list[HourAssessment]]:
    """Split time-ordered slots into contiguous runs (each starts where the previous ended)."""
    out: list[list[HourAssessment]] = []
    for slot in slots:
        if out and out[-1][-1].end == slot.time:
            out[-1].append(slot)
        else:
            out.append([slot])
    return out


def best_blocks(
    groups: list[tuple[list[HourAssessment], int]], limit: int = MAX_BEST_BLOCKS
) -> tuple[list[TimeBlock], bool]:
    """Top non-overlapping blocks of `duration` contiguous slots, by average score (earlier wins ties).

    `groups` pairs each window's usable slots with the block length in slots. If no run is long
    enough anywhere, whole shorter runs are used instead and the flag returned is True.
    """
    full, short = [], []
    for slots, duration in groups:
        for run in runs(slots):
            if len(run) >= duration:
                full += [run[i:i + duration] for i in range(len(run) - duration + 1)]
            else:
                short.append(run)
    candidates = full or short
    candidates.sort(key=lambda c: (-fmean(s.score for s in c), c[0].time))

    chosen: list[list[HourAssessment]] = []
    for c in candidates:
        if all(c[-1].end <= o[0].time or c[0].time >= o[-1].end for o in chosen):
            chosen.append(c)
            if len(chosen) == limit:
                break
    return [block(c, BEST_REASONS) for c in chosen], not full and bool(short)


def avoid_blocks(groups: list[list[HourAssessment]]) -> list[TimeBlock]:
    """Contiguous runs scoring below MARGINAL, each with its main reasons."""
    return [block(run, AVOID_REASONS) for slots in groups for run in runs([s for s in slots if s.score < MARGINAL])]


def block(slots: list[HourAssessment], reason_limit: int) -> TimeBlock:
    average = fmean(s.score for s in slots)
    return TimeBlock(start=slots[0].time, end=slots[-1].end, average_score=round(average),
                     verdict=verdict(round(average)), reasons=block_reasons(slots, reason_limit))


def block_reasons(slots: list[HourAssessment], limit: int) -> list[str]:
    """Hard stops and caps first, then each factor that lost points at its worst hour, worst first."""
    stops = list(dict.fromkeys([stop for s in slots for stop in s.hard_stops]
                               + [s.capped_by for s in slots if s.capped_by]))
    worst: dict[str, FactorScore] = {}
    for s in slots:
        for f in s.factors:
            if f.reason and (f.name not in worst or f.score < worst[f.name].score):
                worst[f.name] = f
    factors = [f.reason for f in sorted(worst.values(), key=lambda f: f.score) if f.reason not in stops]
    return (stops + factors)[:limit]
