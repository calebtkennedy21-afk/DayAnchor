from datetime import date
from types import SimpleNamespace

from data_access import add_surgical_case, update_surgical_case


class DummyStreamlit:
    def __init__(self):
        self.session_state = SimpleNamespace(surgical_cases=[])


def test_add_surgical_case_persists_checklist_fields_in_session_state():
    st_module = DummyStreamlit()

    add_surgical_case(
        case_date=date(2026, 5, 31),
        case_stream="Main OR",
        procedure_name="Ankle arthroscopy",
        anatomical_location="Ankle",
        cpt_codes="29898",
        pt_destination="Summit PT",
        pt_protocol="Ankle scope rehab",
        dme_dispensed="CAM boot",
        post_op_plan="2-week and 6-week follow-up",
        db_enabled_fn=lambda: False,
        st_module=st_module,
    )

    saved_case = st_module.session_state.surgical_cases[0]

    assert saved_case["pt_destination"] == "Summit PT"
    assert saved_case["pt_protocol"] == "Ankle scope rehab"
    assert saved_case["dme_dispensed"] == "CAM boot"
    assert saved_case["post_op_plan"] == "2-week and 6-week follow-up"


def test_update_surgical_case_updates_checklist_fields_in_session_state():
    st_module = DummyStreamlit()
    st_module.session_state.surgical_cases.append(
        {
            "id": 7,
            "procedure_name": "Peroneal tendon repair",
            "pt_destination": "",
            "pt_protocol": "",
            "dme_dispensed": "",
            "post_op_plan": "",
        }
    )

    update_surgical_case(
        7,
        pt_destination="Select PT",
        pt_protocol="Peroneal rehab",
        dme_dispensed="Boot and crutches",
        post_op_plan="2, 6, and 12 week visits",
        db_enabled_fn=lambda: False,
        st_module=st_module,
    )

    saved_case = st_module.session_state.surgical_cases[0]

    assert saved_case["pt_destination"] == "Select PT"
    assert saved_case["pt_protocol"] == "Peroneal rehab"
    assert saved_case["dme_dispensed"] == "Boot and crutches"
    assert saved_case["post_op_plan"] == "2, 6, and 12 week visits"