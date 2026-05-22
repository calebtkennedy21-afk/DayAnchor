from datetime import date

from page_sections import (
    build_bulk_case_option,
    case_library_filter_preset_values,
    case_matches_library_filters,
)


def test_case_library_filter_preset_values_returns_known_preset():
    preset = case_library_filter_preset_values("upcoming_main_or")
    assert preset["stream_filter"] == "Main OR"
    assert preset["date_filter"] == "Next 30 days"
    assert preset["cards_limit"] == 20


def test_case_library_filter_preset_values_falls_back_to_all():
    preset = case_library_filter_preset_values("does_not_exist")
    assert preset["stream_filter"] == "All streams"
    assert preset["date_filter"] == "All dates"


def test_case_matches_library_filters_respects_stream_date_and_query():
    item = {
        "procedure_name": "Ankle arthroscopy",
        "anatomical_location": "Ankle",
        "cpt_codes": "29898",
        "notes": "Extensive debridement",
        "education_notes": "Post-op weight bearing guidance",
        "case_stream": "Main OR",
        "case_date": date(2026, 5, 25),
    }

    assert case_matches_library_filters(
        item,
        stream_filter="Main OR",
        date_filter="Next 30 days",
        normalized_query="arthroscopy",
        today_value=date(2026, 5, 22),
    )

    assert not case_matches_library_filters(
        item,
        stream_filter="TenJet",
        date_filter="Next 30 days",
        normalized_query="arthroscopy",
        today_value=date(2026, 5, 22),
    )

    assert not case_matches_library_filters(
        item,
        stream_filter="Main OR",
        date_filter="Past 30 days",
        normalized_query="arthroscopy",
        today_value=date(2026, 5, 22),
    )

    assert not case_matches_library_filters(
        item,
        stream_filter="Main OR",
        date_filter="Next 30 days",
        normalized_query="tenjet",
        today_value=date(2026, 5, 22),
    )


def test_build_bulk_case_option_formats_human_readable_label():
    item = {
        "id": 17,
        "case_stream": "DSC OR",
        "case_date": date(2026, 5, 23),
        "procedure_name": "Peroneal tendon debridement",
    }

    label = build_bulk_case_option(item)

    assert label == "#17 | DSC OR | 2026-05-23 | Peroneal tendon debridement"


def test_build_bulk_case_option_returns_none_without_id():
    item = {
        "case_stream": "Main OR",
        "case_date": date(2026, 5, 23),
        "procedure_name": "Procedure",
    }

    assert build_bulk_case_option(item) is None
