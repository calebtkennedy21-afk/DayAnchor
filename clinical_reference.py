import re

import streamlit as st


def text_keywords(value):
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())
    tokens = [item for item in cleaned.split() if len(item) > 2]
    stop_words = {
        "and",
        "the",
        "for",
        "with",
        "from",
        "into",
        "procedure",
        "protocol",
        "case",
        "notes",
        "day",
        "dr",
        "bb",
    }
    return [item for item in tokens if item not in stop_words]


def suggest_protocols_for_case(case_item, protocol_documents, max_items=3):
    case_text = " ".join(
        [
            str(case_item.get("procedure_name") or ""),
            str(case_item.get("anatomical_location") or ""),
            str(case_item.get("education_notes") or ""),
            str(case_item.get("notes") or ""),
        ]
    )
    case_terms = set(text_keywords(case_text))
    if not case_terms:
        return []

    ranked = []
    for doc in protocol_documents:
        doc_text = " ".join(
            [
                str(doc.get("protocol_name") or ""),
                str(doc.get("file_name") or ""),
                str(doc.get("notes") or ""),
            ]
        )
        doc_terms = set(text_keywords(doc_text))
        overlap = case_terms.intersection(doc_terms)
        if not overlap:
            continue
        score = len(overlap)
        ranked.append((score, sorted(list(overlap))[:6], doc))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[:max_items]


def anatomy_related_resources(topic_name, topic_terms, surgical_cases, protocol_documents, max_items=4):
    topic_set = set(text_keywords(" ".join(topic_terms)))

    case_ranked = []
    for item in surgical_cases:
        case_text = " ".join(
            [
                str(item.get("procedure_name") or ""),
                str(item.get("anatomical_location") or ""),
                str(item.get("education_notes") or ""),
                str(item.get("notes") or ""),
            ]
        )
        case_terms = set(text_keywords(case_text))
        overlap = sorted(list(case_terms.intersection(topic_set)))
        if overlap:
            case_ranked.append((len(overlap), overlap[:6], item))
    case_ranked.sort(key=lambda item: item[0], reverse=True)

    protocol_ranked = []
    for doc in protocol_documents:
        doc_text = " ".join(
            [
                str(doc.get("protocol_name") or ""),
                str(doc.get("file_name") or ""),
                str(doc.get("notes") or ""),
            ]
        )
        doc_terms = set(text_keywords(doc_text))
        overlap = sorted(list(doc_terms.intersection(topic_set)))
        if overlap:
            protocol_ranked.append((len(overlap), overlap[:6], doc))
    protocol_ranked.sort(key=lambda item: item[0], reverse=True)

    return case_ranked[:max_items], protocol_ranked[:max_items]


def render_anatomy_structure_spotlight(region_name, structures, panel_key):
    st.markdown(f"#### Structure Spotlight: {region_name}")
    structure_names = list(structures.keys())
    choice = st.radio(
        f"Choose a {region_name.lower()} structure",
        structure_names,
        horizontal=True,
        key=f"{panel_key}_spotlight_choice",
        label_visibility="collapsed",
    )
    info = structures[choice]
    cols = st.columns([1.1, 0.9])
    with cols[0]:
        st.markdown(f"**{choice}**")
        st.write(info["summary"])
        st.markdown(
            f"- **Function:** {info['function']}\n"
            f"- **Exam focus:** {info['exam']}\n"
            f"- **Imaging:** {info['imaging']}\n"
            f"- **Procedure/landmark:** {info['procedure']}"
        )
    with cols[1]:
        if info.get("pearls"):
            st.markdown("**Pearls**")
            for pearl in info["pearls"]:
                st.markdown(f"- {pearl}")


def anatomy_structure_map(region_name):
    if region_name == "Foot":
        return {
            "Plantar fascia": {
                "summary": "Primary longitudinal arch stabilizer and common pain generator at the medial calcaneal tubercle.",
                "function": "Maintains arch tension through the windlass mechanism during toe-off.",
                "exam": "Maximal tenderness is often just distal to the medial calcaneal origin.",
                "imaging": "Ultrasound can show fascial thickening and perifascial edema; MRI can show edema or tearing.",
                "procedure": "Relevant for plantar fascia release, injection planning, and heel pain workups.",
                "pearls": ["Morning pain with first steps is classic.", "Dorsiflexion of the hallux tensions the fascia."],
            },
            "First ray and sesamoids": {
                "summary": "The first metatarsal, sesamoids, and hallux complex drive efficient forefoot loading.",
                "function": "Supports push-off and load transfer through the medial forefoot.",
                "exam": "Pain at the sesamoids or first MTP suggests overload, sesamoiditis, or hallux pathology.",
                "imaging": "Weight-bearing radiographs show alignment and sesamoid position; MRI helps with osteochondral and soft-tissue detail.",
                "procedure": "Useful when planning bunion, hallux rigidus, or plantar forefoot procedures.",
                "pearls": ["The first ray should be assessed in stance, not just supine.", "Sesamoid position matters for hallux mechanics."],
            },
            "Posterior tibial tendon": {
                "summary": "Key medial arch support tendon running behind the medial malleolus to the navicular and midfoot.",
                "function": "Inverts and plantarflexes the foot while supporting the medial arch.",
                "exam": "Pain/swelling posterior to the medial malleolus or inability to single-leg heel raise are useful clues.",
                "imaging": "MRI can show tendinosis, split tears, and associated spring ligament failure.",
                "procedure": "Important for flatfoot reconstruction planning and medial column support procedures.",
                "pearls": ["Tibialis posterior failure often changes the whole foot shape.", "Check both tendon and spring ligament together."],
            },
        }
    if region_name == "Ankle":
        return {
            "ATFL": {
                "summary": "The anterior talofibular ligament is the most commonly injured lateral ankle stabilizer.",
                "function": "Resists anterior translation of the talus and contributes to inversion restraint.",
                "exam": "Tenderness just anterior to the lateral malleolus is common after inversion injury.",
                "imaging": "MRI shows fiber discontinuity, edema, and associated CFL or osteochondral injury.",
                "procedure": "Key structure in ankle sprain grading and lateral ligament reconstruction planning.",
                "pearls": ["ATFL injuries often occur first in inversion sprains.", "A positive anterior drawer can point to laxity."],
            },
            "Deltoid complex": {
                "summary": "The medial ligament complex resists valgus tilt and external rotation of the talus.",
                "function": "Stabilizes the medial mortise and supports talar containment.",
                "exam": "Medial ankle pain or widening concerns increase after eversion or rotational trauma.",
                "imaging": "Stress radiographs and MRI help identify deep deltoid disruption and mortise instability.",
                "procedure": "Relevant in syndesmotic, fracture, and ankle instability workups.",
                "pearls": ["Deep deltoid integrity matters for mortise stability.", "Medial pain can coexist with syndesmotic injury."],
            },
            "Syndesmosis": {
                "summary": "The distal tibiofibular syndesmosis keeps the ankle mortise congruent under load.",
                "function": "Maintains fibular spacing and rotational stability during gait.",
                "exam": "Pain above the mortise, squeeze testing, and external rotation pain can be helpful clues.",
                "imaging": "Weight-bearing and stress imaging assess widening; MRI can show ligament disruption.",
                "procedure": "Critical in high ankle sprains and fixation decisions.",
                "pearls": ["Syndesmotic injury often recovers slower than a simple sprain.", "Look for pain proximal to the joint line."],
            },
        }
    if region_name == "Lower Leg":
        return {
            "Gastrocnemius": {
                "summary": "The large superficial calf muscle with medial and lateral heads crossing both knee and ankle.",
                "function": "Powerful plantarflexor and knee flexor during propulsion.",
                "exam": "Tightness and focal tenderness are common with strain or cramping injury.",
                "imaging": "Ultrasound can identify strain or hematoma; MRI maps tears and edema better.",
                "procedure": "Relevant for calf strain care, recession planning, and Achilles-related surgery.",
                "pearls": ["Crosses two joints, so position matters.", "Strains often occur near the musculotendinous junction."],
            },
            "Soleus/Achilles": {
                "summary": "The soleus and Achilles complex are central to endurance plantarflexion and push-off.",
                "function": "Soleus provides sustained plantarflexion; Achilles transmits force to the calcaneus.",
                "exam": "Pain with single-leg heel raise or calf squeeze changes raises concern for tendon pathology.",
                "imaging": "Ultrasound is fast for continuity; MRI is better for insertional and partial-thickness detail.",
                "procedure": "Important for Achilles repair, debridement, and tendon transfer planning.",
                "pearls": ["Insertional disease and midsubstance disease can look different clinically.", "Always check the contralateral side."],
            },
            "Posterior compartment": {
                "summary": "Deep posterior structures include tibialis posterior, FDL, FHL, and the posterior tibial neurovascular bundle.",
                "function": "Provides inversion, toe flexion, and deep supportive control of the arch and gait.",
                "exam": "Deep compartment pain, weakness, or neurovascular symptoms should change the differential.",
                "imaging": "MRI clarifies tendon course and muscle edema; ultrasound can help with tendons near the ankle.",
                "procedure": "Relevant for compartment-focused surgery and tendon pathway orientation.",
                "pearls": ["The posterior tibial artery and tibial nerve travel together.", "Deep posterior pathology can masquerade as Achilles pain."],
            },
        }
    return {
        "ACL": {
            "summary": "Primary anterior translational and rotational stabilizer of the knee.",
            "function": "Limits anterior tibial translation and helps control pivoting motion.",
            "exam": "Lachman testing is one of the most useful bedside assessments.",
            "imaging": "MRI is best for fiber continuity, marrow edema, and associated meniscus injury.",
            "procedure": "Central to reconstruction planning and tunnel placement discussions.",
            "pearls": ["A pivot shift suggests rotational instability.", "ACL and meniscus injuries often coexist."],
        },
        "Meniscus": {
            "summary": "Fibrocartilaginous load-sharing structures between the femur and tibia.",
            "function": "Absorb shock, improve congruence, and contribute to joint stability.",
            "exam": "Joint-line tenderness and mechanical symptoms are common clues.",
            "imaging": "MRI is the main tool for tear pattern and root/root-equivalent detail.",
            "procedure": "Important for repair, meniscectomy, and root repair planning.",
            "pearls": ["Medial tears are often less mobile and more symptomatic.", "Root tears can behave like near-total meniscectomy."],
        },
        "Patellofemoral joint": {
            "summary": "The articulation between the patella and trochlea that drives anterior knee mechanics.",
            "function": "Improves quadriceps leverage and extensor efficiency.",
            "exam": "Pain with stairs, squatting, or prolonged sitting may point here.",
            "imaging": "MRI and axial radiographs can show maltracking, chondral injury, and tilt.",
            "procedure": "Relevant to alignment procedures, cartilage work, and portal planning.",
            "pearls": ["Tracking is dynamic, so assess motion if you can.", "Alignment and soft tissue balance both matter."],
        },
    }
