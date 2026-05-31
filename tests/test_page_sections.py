from datetime import date

from page_sections import (
    build_bulk_case_option,
    case_library_filter_preset_values,
    case_matches_library_filters,
    case_stream_options,
    surgical_case_checklist_summary,
    split_cases_by_status,
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


def test_case_stream_options_includes_only_present_streams():
    cases = [
        {"case_stream": "Main OR"},
        {"case_stream": "TenJet"},
        {"case_stream": "Main OR"},
    ]

    options = case_stream_options(cases)

    assert options == ["All streams", "Main OR", "TenJet"]


def test_case_matches_library_filters_searches_checklist_fields():
    item = {
        "procedure_name": "Achilles repair",
        "anatomical_location": "Ankle",
        "cpt_codes": "27650",
        "notes": "",
        "education_notes": "",
        "pt_destination": "Summit PT",
        "pt_protocol": "Achilles repair rehab",
        "dme_dispensed": "CAM boot",
        "post_op_plan": "2 week and 6 week follow-up scheduled",
        "case_stream": "Main OR",
        "case_date": date(2026, 5, 25),
    }

    assert case_matches_library_filters(
        item,
        stream_filter="Main OR",
        date_filter="Next 30 days",
        normalized_query="summit pt",
        today_value=date(2026, 5, 22),
    )

    assert case_matches_library_filters(
        item,
        stream_filter="Main OR",
        date_filter="Next 30 days",
        normalized_query="cam boot",
        today_value=date(2026, 5, 22),
    )


def test_surgical_case_checklist_summary_counts_completed_items():
    summary = surgical_case_checklist_summary(
        {
            "pt_destination": "Summit PT",
            "pt_protocol": "Flatfoot reconstruction protocol",
            "dme_dispensed": "",
            "post_op_plan": "2-week and 6-week visits booked",
        }
    )

    assert summary["completed_count"] == 3
    assert summary["total_count"] == 4
    assert summary["missing_labels"] == ["DME dispensed"]


def test_split_cases_by_status_groups_expected_cases():
    cases = [
        {"id": 1, "status": "planned"},
        {"id": 2, "status": "completed"},
        {"id": 3, "status": "canceled"},
        {"id": 4, "status": "planned"},
        {"id": 5, "status": "unknown"},
    ]

    groups = split_cases_by_status(cases)

    assert [item["id"] for item in groups["planned"]] == [1, 4]
    assert [item["id"] for item in groups["completed"]] == [2]
    assert [item["id"] for item in groups["canceled"]] == [3]
