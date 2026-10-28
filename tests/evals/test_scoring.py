import pytest

from openrx.evals.scoring import (
    grounding,
    normalize,
    normalize_item,
    numbers_in,
    score_answer,
)
from openrx.evals.tasks import Scoring

CONTAINS = Scoring(type="contains_all")
ORDERED = Scoring(type="ordered")
NUMBER = Scoring(type="number")
BOOLEAN = Scoring(type="boolean")


# --- normalisation --------------------------------------------------------------


def test_normalize_lowercases_and_uses_american_spelling():
    assert normalize("DIARRHOEA, Oedema!") == "diarrhea edema"


def test_normalize_keeps_decimal_points_only():
    assert normalize("It was 12.7%. Done.") == "it was 12.7 done"


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ("Novo Nordisk A/S", "novo nordisk"),
        ("Eli Lilly and Company", "eli lilly"),
        ("Pfizer Inc.", "pfizer"),
        ("Inc", "inc"),  # a suffix alone is kept
    ],
)
def test_normalize_item_drops_company_suffixes(item, expected):
    assert normalize_item(item) == expected


def test_numbers_in_reads_commas_and_decimals():
    assert numbers_in("26,629 reports, 12.7% and -3") == [26629.0, 12.7, -3.0]


# --- contains_all ---------------------------------------------------------------


def test_contains_all_passes_with_other_case_and_spelling():
    score = score_answer("Nausea and diarrhea are most common.", ["NAUSEA", "DIARRHOEA"], CONTAINS)

    assert score.passed and score.score == 1.0


def test_contains_all_gives_partial_credit():
    score = score_answer("Nausea is most common.", ["NAUSEA", "DIARRHOEA"], CONTAINS)

    assert not score.passed and score.score == 0.5
    assert "DIARRHOEA" in score.detail


def test_contains_all_accepts_plurals():
    assert score_answer("Film coated tablets", ["TABLET, FILM COATED"], CONTAINS).passed is False
    assert score_answer("Tablets, film coated", ["TABLET, FILM COATED"], CONTAINS).passed


def test_contains_all_matches_whole_words_only():
    assert not score_answer("Hypernausea", ["NAUSEA"], CONTAINS).passed


def test_contains_all_matches_companies_without_suffix():
    assert score_answer("Made by Novo Nordisk.", ["Novo Nordisk A/S"], CONTAINS).passed


# --- ordered --------------------------------------------------------------------


def test_ordered_passes_in_order():
    answer = "1. Nausea 2. Diarrhoea 3. Fatigue"

    assert score_answer(answer, ["NAUSEA", "DIARRHOEA", "FATIGUE"], ORDERED).passed


def test_ordered_fails_out_of_order_with_partial_credit():
    score = score_answer("Fatigue, nausea, diarrhoea", ["NAUSEA", "DIARRHOEA", "FATIGUE"], ORDERED)

    assert not score.passed and score.detail == "out of order"
    assert score.score == pytest.approx(2 / 3)


def test_ordered_reports_missing_items():
    score = score_answer("Nausea", ["NAUSEA", "FATIGUE"], ORDERED)

    assert not score.passed and score.score == 0.5 and "FATIGUE" in score.detail


# --- number ---------------------------------------------------------------------


def test_number_finds_the_value_in_text():
    assert score_answer("There were 26,629 reports in 2023.", 26629, NUMBER).passed


def test_number_respects_tolerance():
    tolerant = Scoring(type="number", tolerance=0.1)

    assert score_answer("About 12.7% of reports.", 12.66, tolerant).passed
    assert not score_answer("About 13% of reports.", 12.66, tolerant).passed


def test_number_wrong_value_fails():
    score = score_answer("There were 100 reports.", 286, NUMBER)

    assert not score.passed and score.score == 0.0 and "286" in score.detail


# --- boolean --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected", "passed"),
    [
        ("Yes, it carries a boxed warning.", True, True),
        ("No. The label has no boxed warning.", False, True),
        ("The label does not include a boxed warning.", False, True),
        ("Yes.", False, False),
        (
            "**Trial:** semaglutide. **Label:** Yes, it has a boxed warning; not for type 1.",
            True,
            True,
        ),
        ("No matches were found, so the label has no boxed warning.", False, True),
        ("I could not tell.", True, False),
    ],
)
def test_boolean(answer, expected, passed):
    assert score_answer(answer, expected, BOOLEAN).passed is passed


# --- grounding ------------------------------------------------------------------


def test_grounding_counts_numbers_found_in_tool_outputs():
    outputs = ['{"count": 26629, "share": 12.7}']

    assert grounding("26,629 reports, 12.7% died, 999 made up", outputs) == (2, 3)


def test_grounding_ignores_small_numbers():
    assert grounding("Top 5 reactions", []) == (0, 0)
