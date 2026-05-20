from clinical_reference import anatomy_related_resources, suggest_cpt_codes_for_case, suggest_protocols_for_case


def test_suggest_protocols_for_case_uses_cpt_codes_for_matching():
    case_item = {
        "procedure_name": "",
        "anatomical_location": "",
        "cpt_codes": "29898",
        "education_notes": "",
        "notes": "",
    }
    protocol_documents = [
        {
            "id": 1,
            "protocol_name": "Ankle Arthroscopy",
            "file_name": "ankle-arthroscopy.md",
            "notes": "Use when billing CPT 29898 with associated debridement workflow.",
        },
        {
            "id": 2,
            "protocol_name": "Unrelated",
            "file_name": "other.md",
            "notes": "No relevant overlap.",
        },
    ]

    matches = suggest_protocols_for_case(case_item, protocol_documents, max_items=3)

    assert len(matches) == 1
    assert matches[0][2]["id"] == 1


def test_anatomy_related_resources_matches_cases_by_cpt_codes():
    topic_terms = ["29898"]
    surgical_cases = [
        {
            "id": 1,
            "procedure_name": "",
            "anatomical_location": "",
            "cpt_codes": "29898",
            "education_notes": "",
            "notes": "",
        },
        {
            "id": 2,
            "procedure_name": "",
            "anatomical_location": "",
            "cpt_codes": "27658",
            "education_notes": "",
            "notes": "",
        },
    ]

    case_matches, protocol_matches = anatomy_related_resources(
        "Ankle",
        topic_terms,
        surgical_cases,
        protocol_documents=[],
        max_items=4,
    )

    assert len(case_matches) == 1
    assert case_matches[0][2]["id"] == 1
    assert protocol_matches == []


def test_suggest_cpt_codes_for_case_prefers_exact_procedure_and_location():
    case_item = {
        "procedure_name": "Peroneal tendon debridement",
        "anatomical_location": "Ankle",
        "case_stream": "Main OR",
        "education_notes": "",
        "notes": "",
    }
    surgical_cases = [
        {
            "id": 1,
            "procedure_name": "Peroneal tendon debridement",
            "anatomical_location": "Ankle",
            "case_stream": "Main OR",
            "cpt_codes": "27658",
        },
        {
            "id": 2,
            "procedure_name": "Ankle arthroscopy",
            "anatomical_location": "Ankle",
            "case_stream": "Main OR",
            "cpt_codes": "29898",
        },
    ]

    suggestions = suggest_cpt_codes_for_case(case_item, surgical_cases, max_items=2)

    assert len(suggestions) == 2
    assert suggestions[0]["cpt_codes"] == "27658"
    assert suggestions[0]["matched_case_id"] == 1


def test_suggest_cpt_codes_for_case_skips_cases_without_cpt_codes():
    case_item = {
        "procedure_name": "Ankle arthroscopy",
        "anatomical_location": "Ankle",
        "case_stream": "Main OR",
    }
    surgical_cases = [
        {
            "id": 1,
            "procedure_name": "Ankle arthroscopy",
            "anatomical_location": "Ankle",
            "case_stream": "Main OR",
            "cpt_codes": "",
        },
        {
            "id": 2,
            "procedure_name": "Ankle arthroscopy",
            "anatomical_location": "Ankle",
            "case_stream": "Main OR",
            "cpt_codes": "29898",
        },
    ]

    suggestions = suggest_cpt_codes_for_case(case_item, surgical_cases, max_items=3)

    assert len(suggestions) == 1
    assert suggestions[0]["cpt_codes"] == "29898"
    assert suggestions[0]["matched_case_id"] == 2
