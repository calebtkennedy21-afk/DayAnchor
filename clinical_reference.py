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


def _cpt_case_terms(item):
    return set(
        text_keywords(
            " ".join(
                [
                    str(item.get("procedure_name") or ""),
                    str(item.get("anatomical_location") or ""),
                    str(item.get("education_notes") or ""),
                    str(item.get("notes") or ""),
                ]
            )
        )
    )


def suggest_protocols_for_case(case_item, protocol_documents, max_items=3):
    case_text = " ".join(
        [
            str(case_item.get("procedure_name") or ""),
            str(case_item.get("anatomical_location") or ""),
            str(case_item.get("cpt_codes") or ""),
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


def suggest_cpt_codes_for_case(case_item, surgical_cases, max_items=3, cpt_reference=None):
    target_procedure = str(case_item.get("procedure_name") or "").strip().lower()
    target_location = str(case_item.get("anatomical_location") or "").strip().lower()
    target_stream = str(case_item.get("case_stream") or "").strip().lower()
    target_terms = _cpt_case_terms(case_item)

    if not target_procedure and not target_location and not target_terms:
        return []

    ranked = []
    for item in surgical_cases:
        cpt_codes = str(item.get("cpt_codes") or "").strip()
        if not cpt_codes:
            continue

        prior_procedure = str(item.get("procedure_name") or "").strip().lower()
        prior_location = str(item.get("anatomical_location") or "").strip().lower()
        prior_stream = str(item.get("case_stream") or "").strip().lower()
        prior_terms = _cpt_case_terms(item)

        overlap_terms = sorted(list(target_terms.intersection(prior_terms)))
        score = 0
        if target_procedure and target_procedure == prior_procedure:
            score += 6
        if target_location and target_location == prior_location:
            score += 3
        score += len(overlap_terms) * 2
        if target_stream and target_stream == prior_stream:
            score += 1

        if score <= 0:
            continue

        ranked.append(
            (
                score,
                overlap_terms[:6],
                cpt_codes,
                {
                    "matched_case_id": item.get("id"),
                    "matched_procedure_name": item.get("procedure_name") or "",
                    "matched_category": "Historical case",
                    "match_source": "historical",
                },
            )
        )

    for ref_item in cpt_reference or []:
        code = str(ref_item.get("code") or "").strip()
        description = str(ref_item.get("description") or "").strip()
        category = str(ref_item.get("category") or "").strip()
        if not code or not description:
            continue

        combined_reference_text = f"{category} {description}".lower()
        reference_terms = set(text_keywords(combined_reference_text))
        overlap_terms = sorted(list(target_terms.intersection(reference_terms)))
        score = len(overlap_terms) * 2
        if target_location and target_location in combined_reference_text:
            score += 2
        if target_procedure and target_procedure in description.lower():
            score += 6

        if score <= 0:
            continue

        ranked.append(
            (
                score,
                overlap_terms[:6],
                code,
                {
                    "matched_case_id": None,
                    "matched_procedure_name": description,
                    "matched_category": category,
                    "match_source": "reference",
                },
            )
        )

    ranked.sort(key=lambda item: item[0], reverse=True)

    deduped = []
    seen_codes = set()
    for score, overlap_terms, cpt_codes, item in ranked:
        if cpt_codes in seen_codes:
            continue
        seen_codes.add(cpt_codes)
        deduped.append(
            {
                "cpt_codes": cpt_codes,
                "score": score,
                "overlap_terms": overlap_terms,
                "matched_case_id": item.get("matched_case_id"),
                "matched_procedure_name": item.get("matched_procedure_name") or "",
                "matched_category": item.get("matched_category") or "",
                "match_source": item.get("match_source") or "historical",
            }
        )
        if len(deduped) >= max_items:
            break

    return deduped


def anatomy_related_resources(topic_name, topic_terms, surgical_cases, protocol_documents, max_items=4):
    topic_set = set(text_keywords(" ".join(topic_terms)))

    case_ranked = []
    for item in surgical_cases:
        case_text = " ".join(
            [
                str(item.get("procedure_name") or ""),
                str(item.get("anatomical_location") or ""),
                str(item.get("cpt_codes") or ""),
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


def filter_anatomy_xray_images(images, body_part_filter="All", fracture_filter="All", view_filter="All", query=""):
    normalized_query = str(query or "").strip().lower()
    filtered = []

    for item in images or []:
        body_part = str(item.get("body_part") or "").strip()
        fracture_type = str(item.get("fracture_type") or "").strip()
        view_label = str(item.get("view_label") or "").strip()

        if body_part_filter != "All" and body_part != body_part_filter:
            continue
        if fracture_filter != "All" and fracture_type != fracture_filter:
            continue
        if view_filter != "All" and view_label != view_filter:
            continue

        if normalized_query:
            searchable_text = " ".join(
                [
                    body_part,
                    fracture_type,
                    view_label,
                    str(item.get("notes") or ""),
                    str(item.get("image_name") or ""),
                ]
            ).lower()
            if normalized_query not in searchable_text:
                continue

        filtered.append(item)

    return filtered


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


def anatomy_bones_map(region_name):
    """Detailed osteology and skeletal anatomy reference."""
    if region_name == "Foot":
        return {
            "Tarsal bones—Hindfoot": {
                "summary": "The talus and calcaneus form the foundation of the foot, bearing weight and transmitting forces.",
                "anatomy": "Talus: Trochlea articulates with tibia/fibula; head and neck angle medially toward the navicular. Calcaneus: Largest tarsal bone with posterior tuberosity (weight-bearing heel) and anterior articulation with cuboid.",
                "landmarks": "Medial talus bulge, calcaneal tuberosity, posterior calcaneal process, lateral process (Gissane's angle).",
                "function": "Weight transmission, shock absorption, and rotational coupling during gait.",
                "imaging": "Weight-bearing radiographs show alignment; Gissane's angle assesses posterior facet orientation. CT clarifies fracture patterns.",
                "procedure": "Critical for hindfoot arthrodesis, calcaneal osteotomy, and talus preservation planning.",
                "pearls": ["Calcaneal pitch and talar tilt are key alignment parameters.", "Talus position changes with pes planus vs cavus.", "CT is essential for subtalar joint fractures."],
            },
            "Tarsal bones—Midfoot": {
                "summary": "Navicular, cuboid, and cuneiforms bridge hindfoot and forefoot, controlling arch geometry.",
                "anatomy": "Navicular: Boat-shaped, articulates with talus head, three cuneiforms, and cuboid. Cuboid: Lateral midfoot articulator with calcaneus and lateral metatarsals. Cuneiforms (medial, intermediate, lateral): Support first, second, and third metatarsals respectively.",
                "landmarks": "Navicular tuberosity (common pain point), talonavicular joint, naviculocuneiform articulation, cuboid groove for peroneus longus.",
                "function": "Distribute loads across medial and lateral columns; support arch rigidity and spring ligament complex.",
                "imaging": "AP, lateral, and oblique radiographs; CT for joint detail. MRI shows spring ligament and midfoot ligament disruption.",
                "procedure": "Important for hallux limitus, flatfoot, and midfoot ulcer/arthropathy management.",
                "pearls": ["Spring ligament (deltoid complex) is critical to medial column support.", "Cuboid syndrome involves cuboid-metatarsal hypomobility.", "Midfoot Lisfranc complex requires high clinical suspicion."],
            },
            "Metatarsals (rays I–V)": {
                "summary": "Five metatarsals serve as the lever arms for propulsion and weight distribution across the forefoot.",
                "anatomy": "Each has a base (proximal), shaft, neck, and head. First ray (hallux metatarsal): Shorter, thicker, bears higher load. Second ray (longest): Most mobile. Third–fifth rays: Progressively shorter and contribute to lateral column stability.",
                "landmarks": "Metatarsal heads, bases, intermetatarsal spaces (MTJ anatomy), tuberosities, and sesamoid articulations on first and second heads.",
                "function": "Load distribution during stance and propulsion; windlass mechanism with plantar fascia during toe-off.",
                "imaging": "Weight-bearing radiographs show metatarsal parabola and first-ray position. MRI or ultrasound for peroneal longus at cuboid and MTJ synovitis.",
                "procedure": "Relevant for metatarsal osteotomy, bunion, hallux rigidus, metatarsalgia, and forefoot ulcer management.",
                "pearls": ["First ray should dorsiflex with toe extension (dorsal mobility assessment).", "Metatarsal parabola guides forefoot surgery.", "Sesamoid position reflects first-ray mechanics."],
            },
            "Phalanges and sesamoids": {
                "summary": "Proximal, middle, and distal phalanges form the toes; sesamoids amplify first MTP mechanics.",
                "anatomy": "Hallux: Two phalanges (proximal and distal); lateral and medial sesamoids under the first MTP head. Digits 2–5: Three phalanges each (proximal, middle, distal) with interphalangeal joints.",
                "landmarks": "First MTP joint, sesamoid positions (medial vs lateral), IP joint flexor creases, and toe pulp pressure points.",
                "function": "Fine balance and stability; sesamoids protect first MTP tendon and improve flexor moment arm.",
                "imaging": "Radiographs (axial views for sesamoid position, weight-bearing for first MTP assessment). MRI for sesamoiditis or hallux pathology.",
                "procedure": "Important for hallux rigidus, hallux limitus, sesamoiditis, and turf toe management.",
                "pearls": ["Hallux rigidus is dorsal OA at first MTP.", "Sesamoid stress fractures are rare but painful.", "Turf toe involves plantar plate and collateral ligament failure."],
            },
        }
    if region_name == "Ankle":
        return {
            "Tibia and fibula—Distal shafts": {
                "summary": "The distal tibia and fibula converge to form the ankle mortise, the socket for the talus.",
                "anatomy": "Distal tibia: Medial malleolus, tibial plafond (trochlea), anterior margin, and posterior lip. Distal fibula: Lateral malleolus, lateral peroneal groove. Syndesmotic ligaments (AITFL, PITFL, interosseous membrane) maintain spacing.",
                "landmarks": "Medial malleolus (easily palpable), lateral malleolus (fibular prominence), anterior tibial edge, syndesmotic interval (high ankle region).",
                "function": "Weight-bearing articulation and dynamic mortise stability during rotation and dorsiflexion.",
                "imaging": "Weight-bearing radiographs assess alignment; CT for complex fractures. Stress radiographs for syndesmotic widening. MRI for syndesmotic and tibiofibular ligament detail.",
                "procedure": "Essential for fracture classification, syndesmotic fixation, and ankle reconstruction.",
                "pearls": ["Medial malleolus fractures are common and often involve deltoid ligament.", "Syndesmotic injury recovery is slower than simple ankle sprains.", "Mortise congruence is key to long-term outcomes."],
            },
            "Talus": {
                "summary": "The keystone bone of the ankle, articulating with the mortise above and calcaneus/navicular below.",
                "anatomy": "Superior surface (trochlea): Broader anteriorly, narrower posteriorly. Medial and lateral facets for mortise stability. Head and neck angle medially toward navicular. Posterior process with medial/lateral tubercles.",
                "landmarks": "Talar dome, anterior talus (palpable just distal to ankle joint line), posterior process (talus posterior), talar shoulders.",
                "function": "Primary weight-bearing and load-transmission through the ankle joint; couples with hindfoot rotation.",
                "imaging": "Radiographs (AP, lateral, mortise views for alignment). CT essential for osteochondral lesions, fractures, and posterior process abnormalities. MRI shows cartilage and ligament attachments.",
                "procedure": "Critical for ankle replacement, osteochondral repair, lateral process fracture, and talar tilt assessment.",
                "pearls": ["Talar dome articular cartilage is high-pressure and prone to OCD.", "Posterior process impingement can mimic Achilles pathology.", "Talus has limited blood supply; fractures may lead to AVN."],
            },
            "Calcaneus—Posterior and medial surfaces": {
                "summary": "The heel bone interfaces with the talus via the subtalar joint and supports the ankle architecture.",
                "anatomy": "Posterior tuberosity: Weight-bearing prominence. Sustentaculum tali: Medial shelf supporting the talar head. Subtalar joint facets (posterior, middle, anterior). Calcaneal body extends to anterior cuboid articulation.",
                "landmarks": "Calcaneal tuberosity (heel), medial aspect for spring ligament attachment, cuboid groove on anterior surface.",
                "function": "Heel strike absorption, weight transmission to midfoot, and hindfoot inversion/eversion coupling.",
                "imaging": "Weight-bearing radiographs show alignment and calcaneal pitch. CT clarifies fractured subtalar joint anatomy. MRI for soft-tissue and plantar fascia detail.",
                "procedure": "Important for heel pain, Haglund deformity, lateral process fracture, and calcaneal osteotomy.",
                "pearls": ["Calcaneal fractures often involve subtalar joint disruption.", "Calcaneal pitch determines arch mechanics.", "Haglund deformity is a posterior tuberosity enlargement."],
            },
            "Navicular—Head articulation": {
                "summary": "The navicular receives the talar head, creating a critical medial column coupling point.",
                "anatomy": "Talonavicular joint (spheroid articulation), navicular-cuneiform joints distally, medial tuberosity for posterior tibial tendon insertion.",
                "landmarks": "Navicular tuberosity (palpable medially), talonavicular joint line.",
                "function": "Medial column load transfer, arch geometry, and posterior tibial tendon action point.",
                "imaging": "MRI best shows spring ligament failure and talonavicular alignment. Radiographs assess medial column height.",
                "procedure": "Relevant for flatfoot, posterior tibial tendon dysfunction, and medial column reconstruction.",
                "pearls": ["Talonavicular coverage reflects medial column stability.", "Spring ligament is the dynamic medial support.", "Navicular stress fractures are rare but serious."],
            },
        }
    if region_name == "Lower Leg":
        return {
            "Tibia": {
                "summary": "The weight-bearing bone of the lower leg, transmitting femoral load through the knee to the ankle.",
                "anatomy": "Proximal end: Medial and lateral condyles, tibial plateau, anterior tuberosity (patellar tendon insertion). Shaft: Anterior and medial borders, nutrient foramen. Distal end: Medial malleolus, tibial plafond.",
                "landmarks": "Tibial tuberosity (easily palpable anteriorly), anterior tibial crest, medial tibial border, distal medial malleolus.",
                "function": "Primary weight bearing; provides attachment for muscles (quadriceps, tibialis anterior, tibialis posterior) and ligaments (ACL, MCL).",
                "imaging": "Radiographs show alignment, physeal anatomy (in skeletally immature), and stress fractures. MRI for compartment syndrome and marrow edema.",
                "procedure": "Critical for knee arthroplasty, tibial plateau fractures, tibial tuberosity advancement, and stress fracture management.",
                "pearls": ["Anterior tibial stress fractures are common in runners (posteromedial tibia).", "Tibial tuberosity apophysitis (Osgood-Schlatter) occurs in adolescents.", "Proximal tibiofibular joint (PTFJ) moves with knee flexion."],
            },
            "Fibula": {
                "summary": "Non-weight-bearing bone parallel to the tibia, providing muscle attachment and ankle stabilization.",
                "anatomy": "Proximal end: Fibular head and neck (just distal to knee joint line). Shaft: Slender, nutrient foramen. Distal end: Lateral malleolus, peroneal groove.",
                "landmarks": "Fibular head (lateral knee prominence), lateral fibular border, distal lateral malleolus.",
                "function": "Muscle attachment (biceps femoris, peroneal muscles, soleus); lateral ankle and syndesmotic stabilization.",
                "imaging": "Often incidental on leg radiographs; important for ankle trauma assessment. Proximal fibular stress injuries less common than tibial.",
                "procedure": "Relevant for ankle syndesmotic injuries, peroneal nerve palsy, and lateral ankle reconstruction.",
                "pearls": ["Peroneal nerve wraps around fibular neck (common site of compression).", "Proximal tibiofibular joint can dislocate.", "Fibular head moves anterolaterally with knee flexion."],
            },
            "Interosseous membrane and syndesmosis": {
                "summary": "The tough fascial sheet connecting tibia and fibula, maintaining spacing and transmitting forces.",
                "anatomy": "Interosseous membrane spans the tibial and fibular shafts. Proximal tibiofibular joint (PTFJ) with its own capsule and ligaments. Distal syndesmosis (AITFL, PITFL, interosseous ligament).",
                "landmarks": "Central membrane visible on cross-section. Anterior and posterior tibiofibular ligament attachments near ankle.",
                "function": "Load sharing between tibia and fibula; rotational control during knee and ankle motion.",
                "imaging": "MRI best shows membrane and syndesmotic ligament detail. Ultrasound can assess fascial continuity.",
                "procedure": "Important for proximal PTFJ dislocation, syndesmotic injury, and compartment syndrome decompression.",
                "pearls": ["Interosseous membrane is load-bearing (transfers ~16% of load).", "Syndesmotic injuries require imaging and careful reduction.", "Fasciotomy releases interosseous membrane and fascia."],
            },
        }
    return {
        "Distal femur": {
            "summary": "The knee's upper articulation, with medial and lateral condyles forming the tibiofemoral joint.",
            "anatomy": "Medial femoral condyle: Broader, more posterior. Lateral femoral condyle: Narrower, more anterior. Intercondylar groove (trochlea) for patellar tracking. Epicondyles (adductors, collaterals). Intercondylar notch (ACL/PCL attachments).",
            "landmarks": "Medial and lateral femoral epicondyles (easily palpable), distal femoral line alignment.",
            "function": "Primary femoral articulation, weight distribution, and attachment for cruciate and collateral ligaments.",
            "imaging": "Radiographs show alignment (varus/valgus), distal femoral anatomy. MRI for cartilage, PCL, and ACL attachments.",
            "procedure": "Essential for knee arthroplasty, distal femoral osteotomy, and fracture management.",
            "pearls": ["Valgus knee alignment increases medial compartment stress.", "Distal femoral fractures often involve the articular surface.", "Femoral tunnel placement in ACL reconstruction critical."],
        },
        "Proximal and distal tibia": {
            "summary": "The tibia forms the main tibiofemoral and ankle articulations, bridging knee and ankle.",
            "anatomy": "Proximal tibia: Medial and lateral tibial plateaus, intercondylar eminence (ACL/meniscal attachment), anterior tuberosity. Distal tibia: Tibial plafond (ankle trochlea), medial malleolus, medial and posterior borders.",
            "landmarks": "Tibial tuberosity (patellar tendon insertion, easily palpable), tibial crest, medial tibial shaft (common stress fracture site).",
            "function": "Primary weight bearing from femur to ankle; major muscle attachment site (quads, hamstrings, gastrocnemius, tibialis anterior/posterior).",
            "imaging": "Radiographs show alignment and fracture patterns. MRI for plateau fractures, metaphyseal edema, and stress fractures.",
            "procedure": "Critical for knee arthroplasty, tibial plateau fracture repair, and tibial tuberosity transfer.",
            "pearls": ["Tibial plateau fractures often have meniscal tears.", "Posteromedial tibial stress fractures are common in runners.", "Tibial version (internal vs external) affects knee mechanics."],
        },
        "Patella and trochlea": {
            "summary": "The patella is a sesamoid bone within the quadriceps tendon, gliding within the femoral trochlea.",
            "anatomy": "Patella: Apex (inferior), base (superior), articular facets (medial and lateral). Trochlea: Medial and lateral femoral grooves. Patellofemoral joint is the most mobile knee articulation.",
            "landmarks": "Patellar apex and medial/lateral facets (felt under skin when knee is extended), trochlear groove (felt under patella).",
            "function": "Improves quadriceps mechanical advantage; load distribution across patellofemoral joint during knee bending and knee extension.",
            "imaging": "Axial radiographs (Merchant, sunrise) assess patellar alignment and tilt. MRI shows cartilage, maltracking, and soft-tissue imbalance.",
            "procedure": "Important for patellofemoral arthritis, maltracking, and patellar realignment surgery.",
            "pearls": ["Lateral patellar tilt and medial patellar facet compression are common OA patterns.", "Q-angle and tibial tuberosity–trochlear groove (TT-TG) distance guide realignment.", "Patellofemoral pain is often related to muscle imbalance rather than structural abnormality."],
        },
        "Fibula and knee articulation": {
            "summary": "The fibula's proximal head articulates with the tibia just below the knee, contributing to lateral stability.",
            "anatomy": "Fibular head: Oval articular surface, posterior and superior to tibial plateau. Common fibular nerve wraps around fibular neck. Fibular collateral ligament (FCL) attaches to fibular head.",
            "landmarks": "Fibular head (easily palpable lateral to knee joint line), fibular neck (just distal to head).",
            "function": "Lateral knee stabilization via FCL and posterolateral corner (PLC) attachment; provides attachment for biceps femoris.",
            "imaging": "Standard knee radiographs show fibular alignment. MRI assesses FCL and PLC integrity.",
            "procedure": "Important for proximal tibiofibular joint (PTFJ) dislocation, FCL repair, and PLC reconstruction.",
            "pearls": ["Fibular head dislocation is rare but can cause common peroneal nerve compression.", "PTFJ can dislocate anteriorly or posteriorly.", "FCL integrity is assessed with varus stress and dial testing."],
        },
    }


def anatomy_fractures_map(region_name):
    """Fracture types, locations, mechanisms, and clinical significance."""
    if region_name == "Foot":
        return {
            "Calcaneal fractures": {
                "location": "Posterior calcaneal tuberosity and body; most common at the subtalar joint (posterior facet).",
                "mechanism": "High-energy trauma (fall from height, motor vehicle collision); axial loading during plantarflexion.",
                "types": "Intra-articular (most common, involve posterior subtalar facet), extra-articular (tuberosity, anterior process, medial process, lateral process).",
                "clinical": "Severe heel pain, swelling, ecchymosis, inability to bear weight. May have associated injuries (lumbar spine, pelvis).",
                "imaging": "Radiographs (lateral, Broden's views); CT essential for fracture pattern, Gissane's angle, and joint involvement.",
                "treatment": "Non-operative for minimally displaced; operative for displaced intra-articular (open reduction internal fixation with plate and screws).",
                "complications": "Subtalar arthritis, wound complications, peroneal tendon irritation, chronic pain.",
                "pearls": ["Bilateral calcaneal fractures in up to 10% of cases; always image both sides.", "Gissane's angle and Böhler's angle assess severity.", "Early mobilization is key even in conservative management."],
            },
            "Talus fractures": {
                "location": "Talar neck, talar body, posterior process, lateral process, talar dome (osteochondral lesions).",
                "mechanism": "Neck fractures from dorsiflexion trauma; body fractures from high-energy impact; dome lesions from inversion or axial loading.",
                "types": "Hawkins classification for neck fractures (I-IV based on displacement); dome lesions (medial vs lateral).",
                "clinical": "Severe pain, swelling, limited ankle motion. Neck fractures may present with subtle radiographic findings.",
                "imaging": "Radiographs (AP, lateral, mortise, 30° internal rotation for neck detail); CT for fracture pattern and displacement; MRI for osteochondral lesions.",
                "treatment": "Non-operative for minimally displaced; operative for displaced neck fractures (surgical fixation to reduce AVN risk).",
                "complications": "Avascular necrosis (high risk with displaced neck fractures, especially Hawkins III-IV), post-traumatic arthritis, chronic pain.",
                "pearls": ["Talar neck fractures have high AVN risk due to limited blood supply.", "Hawkins sign (subchondral lucency) on radiograph at 6-12 weeks indicates revascularization (good prognosis).", "Posterior process fractures can be confused with os trigonum."],
            },
            "Navicular fractures": {
                "location": "Navicular body, tuberosity, dorsal lip, stress fractures (central and stress patterns).",
                "mechanism": "Body fractures from direct trauma or high-energy twisting; tuberosity avulsion (posterior tibial tendon); stress fractures from overuse (central 1/3 most common).",
                "types": "Acute body fractures, tuberosity fractures, stress fractures (most common in athletes).",
                "clinical": "Medial foot pain, swelling at navicular tuberosity, difficulty walking. Stress fractures present with insidious onset and activity-related pain.",
                "imaging": "Radiographs (AP, lateral, oblique); CT or MRI for stress fractures and fracture pattern detail; MRI shows early stress fractures before radiographic changes.",
                "treatment": "Stress fractures: aggressive immobilization (6-8 weeks non-weightbearing is standard). Acute body fractures: depends on displacement; often require surgical fixation.",
                "complications": "Non-union, malunion, chronic pain, post-traumatic arthritis, recurrent stress fractures if return to activity too early.",
                "pearls": ["Central 1/3 stress fractures are notorious for delayed healing and non-union.", "High-risk zone fractures need aggressive conservative care.", "Consider bilateral imaging; up to 30% are bilateral."],
            },
            "Fifth metatarsal base fracture (Jones fracture)": {
                "location": "Metaphyseal-diaphyseal junction of fifth metatarsal (at the tuberosity but proximal to the articular surface).",
                "mechanism": "Inversion with plantarflexion, repetitive microtrauma in athletes. Occurs 1-1.5 cm distal to tuberosity (different from dancer's fracture at the tuberosity).",
                "types": "Acute Jones fracture, chronic stress fracture, non-union.",
                "clinical": "Lateral foot and midfoot pain, swelling, difficulty bearing weight, especially in athletes and dancers.",
                "imaging": "Radiographs (AP, oblique); CT or MRI for stress fracture detection before radiographic changes appear.",
                "treatment": "Immobilization for acute fractures; high-risk non-union mandates surgical fixation (screw fixation or plate).",
                "complications": "Non-union (up to 20-25% with conservative management), recurrent fractures, chronic pain.",
                "pearls": ["Jones fracture is high-risk for non-union due to poor blood supply to metaphyseal-diaphyseal zone.", "Early surgical fixation (intramedullary screw) recommended for athletes wanting faster return-to-play.", "Distinguish from tuberosity avulsion (lateral process) which has better prognosis."],
            },
            "Lisfranc injury": {
                "location": "Tarsometatarsal (TMT) joint complex; involves ligaments between cuboid-metatarsals and cuneiforms-metatarsals.",
                "mechanism": "High-energy twist or crush injury; can occur from low-energy mechanisms (missed diagnosis common). Midfoot twisting during weight-bearing.",
                "types": "Homolateral (all metatarsals displaced together), isolated (one or two rays), divergent (lateral rays displace laterally).",
                "clinical": "Midfoot pain, swelling, point tenderness at midfoot, difficulty bearing weight. May appear mild initially despite significant ligament injury.",
                "imaging": "Weight-bearing radiographs essential (AP, lateral, oblique views show alignment); stress views if subtle; CT for fracture patterns; MRI for ligament detail.",
                "treatment": "Non-displaced, ligamentous: immobilization (4-6 weeks); displaced or unstable: surgical fixation (screws, plates).",
                "complications": "Post-traumatic arthritis, chronic midfoot pain, chronic instability, missed diagnosis leading to chronic disability.",
                "pearls": ["High index of suspicion needed; easily missed initially.", "Weight-bearing radiographs are crucial.", "Medial cuneiform-metatarsal distance >5-6 mm indicates instability.", "Surgical fixation recommended for most displaced injuries."],
            },
        }
    if region_name == "Ankle":
        return {
            "Lateral malleolus fracture": {
                "location": "Distal fibula; classically at or above syndesmotic level (Lauge-Hansen classification).",
                "mechanism": "Inversion ankle sprain with rotational component. Weber classification: A (below syndesmosis, low energy), B (at syndesmotic level, rotation), C (above syndesmosis, syndesmotic ligament injury).",
                "types": "Weber A (50-60%), Weber B (15-20%), Weber C (10-15%); often occurs with medial-sided injury (deltoid ligament or medial malleolus fracture).",
                "clinical": "Lateral ankle pain, swelling, bruising, tenderness over lateral malleolus, difficulty bearing weight.",
                "imaging": "Radiographs (AP, lateral, mortise views); assess talar tilt and medial clear space. CT if complex fracture or surgical planning needed.",
                "treatment": "Weber A: conservative (boot, early mobilization). Weber B/C: often require surgery if unstable (assessed by medial clear space and ligament injury).",
                "complications": "Ankle instability if inadequately treated, post-traumatic arthritis, chronic pain, syndesmotic injury if Weber C.",
                "pearls": ["Medial injury assessment is critical; may need deltoid ligament repair at time of lateral fixation.", "Syndesmotic screws (if Weber C) must be removed before ankle motion exercises.", "High rate of missed syndesmotic injuries; get mortise and external rotation views."],
            },
            "Medial malleolus fracture": {
                "location": "Distal tibia medial malleolus; may be associated with lateral malleolus or syndesmotic injury.",
                "mechanism": "Eversion and external rotation; often part of a bimalleolar or trimalleolar fracture complex.",
                "types": "Vertical fractures (Lauge-Hansen supination-external rotation), horizontal or oblique, avulsion (rare, small chip).",
                "clinical": "Medial ankle pain, swelling medially, tenderness over medial malleolus, often significant ankle instability if bimalleolar.",
                "imaging": "Radiographs (AP, lateral, mortise); mortise view shows displacement. CT useful for complex fractures and pre-operative planning.",
                "treatment": "Non-displaced: conservative (boot). Displaced: surgical fixation (screw or plate) to restore mortise anatomy.",
                "complications": "Tibiotalar arthritis if malreduction, chronic ankle pain, residual instability.",
                "pearls": ["Always assess for bimalleolar or trimalleolar fractures.", "Reduction must be anatomic to prevent arthritis.", "Medial approach for fixation; avoid posterior tibial tendon."],
            },
            "Syndesmotic injury (high ankle sprain/fracture)": {
                "location": "Anterior inferior tibiofibular ligament (AITFL), posterior inferior tibiofibular ligament (PITFL), interosseous membrane.",
                "mechanism": "External rotation and abduction of foot on fixed tibia; higher-energy mechanism than simple ankle sprain.",
                "types": "Ligamentous (syndesmotic sprain), associated fibular fracture above syndesmosis (Weber C), complete diastasis.",
                "clinical": "Pain above ankle joint line, tenderness at syndesmosis, squeeze test positive (pain with bilateral fibular compression), painful external rotation.",
                "imaging": "Weight-bearing mortise views; widened medial clear space (>5-6 mm) or increased tibiofibular clear space indicates injury. CT for fracture patterns; MRI for ligament detail.",
                "treatment": "Non-operative: immobilization, early mobilization. Operative: syndesmotic screw or suture-button fixation if unstable or displaced.",
                "complications": "Chronic syndesmotic pain, ankle instability, post-traumatic arthritis, malunion if inadequately reduced.",
                "pearls": ["Syndesmotic injuries recover slower than simple ankle sprains.", "Screw must be removed before ankle motion exercises (typically at 6-8 weeks).", "Suture-button has less stiffness and may allow earlier motion.", "Medial clear space >5 mm or >2 mm increase from contralateral side suggests injury."],
            },
            "Talus fracture (osteochondral lesion of dome)": {
                "location": "Talar dome, most commonly anterolateral (60%) and posteromedial (40%) surfaces.",
                "mechanism": "Inversion ankle sprain with dorsiflexion (anterolateral) or plantarflexion with inversion (posteromedial).",
                "types": "Acute cartilage injury, chondral fracture (no bone), osteochondral fracture (bone and cartilage).",
                "clinical": "Ankle pain, swelling, mechanical symptoms (catching, locking) may be late findings, often initially attributed to simple ankle sprain.",
                "imaging": "Radiographs may appear normal or show subtle lucency; CT or MRI best for diagnosis (MRI most sensitive for chondral injury).",
                "treatment": "Non-operative: conservative management for acute injuries. Operative: arthroscopic debridement, drilling, osteochondral transfer (if large, uncontained).",
                "complications": "Chronic ankle pain, mechanical symptoms, post-traumatic arthritis, loose body formation.",
                "pearls": ["Often missed on initial evaluation.", "MRI is gold standard for diagnosis.", "Large lesions (>150 mm²) or uncontained lesions may need surgical intervention.", "Posteromedial lesions tend to be larger and deeper."],
            },
        }
    if region_name == "Lower Leg":
        return {
            "Tibia shaft fracture": {
                "location": "Tibial shaft (proximal, middle, distal third); classified by location and fracture pattern.",
                "mechanism": "High-energy trauma (motor vehicle collision, fall from height); low-energy in pathologic fractures.",
                "types": "Transverse, spiral, comminuted, segmental; open (Gustilo-Anderson grading) or closed.",
                "clinical": "Severe pain, swelling, deformity, inability to bear weight. Compartment syndrome risk; assess leg swelling, pain with passive motion, sensory/motor changes.",
                "imaging": "Radiographs (AP, lateral) including knee and ankle joints; CT for complex patterns; consider compartment syndrome workup if clinical suspicion.",
                "treatment": "Closed fractures: often intramedullary nail fixation (gold standard). Open fractures: urgent wound management, temporary fixation, staged definitive fixation.",
                "complications": "Compartment syndrome, non-union, malunion, infection (especially open fractures), vascular injury, peroneal nerve injury.",
                "pearls": ["Always rule out compartment syndrome clinically and with fasciotomy if indicated.", "Open fractures require urgent orthopedic consultation.", "Spiral fractures have higher non-union risk than transverse.", "Segmental fractures (two separate zones) have worse prognosis."],
            },
            "Fibula shaft fracture": {
                "location": "Fibular shaft; often associated with tibia fracture but can occur in isolation.",
                "mechanism": "Direct blow, traction injury, or associated with tibia fracture (more common).",
                "types": "Isolated fibula fracture (proximal, middle, distal), combined tibia-fibula fracture.",
                "clinical": "Lateral leg pain, swelling, ecchymosis. Assess for common peroneal nerve injury (foot drop, sensory loss dorsum of foot).",
                "imaging": "Radiographs (AP, lateral) of entire lower leg. Always assess for associated tibia injury (syndesmotic injury if distal third).",
                "treatment": "Isolated distal fibula: often conservative (if no syndesmotic injury). Associated with tibia: treated with tibia fixation.",
                "complications": "Common peroneal nerve palsy, compartment syndrome (rare with isolated injury), non-union (rare), ankle instability if syndesmotic involvement.",
                "pearls": ["Always assess peroneal nerve (deep and superficial branches).", "Distal fibula fracture may indicate syndesmotic injury; assess medial clear space.", "Isolated proximal fibula fracture may indicate anterolateral knee ligament injury or Maisonneuve injury."],
            },
            "Stress fracture (tibia/fibula)": {
                "location": "Tibia: posteromedial shaft (most common, 50-60%), anterior tibial cortex (high-risk), fibula: medial proximal fibula (less common).",
                "mechanism": "Repetitive overload, training errors, poor biomechanics, inadequate recovery in runners and athletes.",
                "types": "Posteromedial tibia (low-risk, good prognosis), anterior tibia (high-risk, non-union risk), fibular stress fracture.",
                "clinical": "Insidious onset of shin pain with running, gradually worsens, pain along medial tibia or anterior tibia depending on location.",
                "imaging": "Radiographs may be normal early (get at 2-3 weeks for callus); MRI or CT best for early detection (shows marrow edema, fracture line).",
                "treatment": "Rest, ice, immobilization, gradual return to activity. Anterior tibia and fibular fractures may need casting (6-8 weeks). Surgical fixation if non-union.",
                "complications": "Non-union (especially anterior tibia), delayed healing, refracture if return to activity too quickly.",
                "pearls": ["Posteromedial tibia fractures can return to activity sooner (4-6 weeks) vs anterior tibia (8-12 weeks) or fibular fractures.", "MRI is most sensitive and shows severity (grade I-IV).", "Grade III-IV or anterior location may benefit from immobilization or even surgical fixation."],
            },
            "Proximal tibiofibular joint (PTFJ) dislocation": {
                "location": "Proximal tibiofibular articulation, just distal to knee joint.",
                "mechanism": "Anterolateral (more common) from knee varus or external tibial rotation; posterolateral or posteromedial less common.",
                "types": "Anterolateral dislocation (most common), posterolateral, posteromedial, isolated or with other knee ligament injury.",
                "clinical": "Lateral knee pain, palpable fibular head prominence (anteriorly or posteriorly), may have associated knee ligament injury symptoms.",
                "imaging": "Radiographs (AP, lateral knee views) may show dislocation. CT useful for identifying associated fractures. Always assess for peroneal nerve injury clinically.",
                "treatment": "Reduction (gentle longitudinal traction with derotation) under sedation or general anesthesia. Immobilization post-reduction. Surgery if recurrent.",
                "complications": "Common peroneal nerve injury (peroneal nerve palsy causing foot drop), recurrent dislocation, PTFJ arthritis.",
                "pearls": ["Peroneal nerve injury occurs in ~50% of acute dislocations.", "Reduction usually successful with manipulation; rarely requires surgery initially.", "Recurrent PTFJ dislocation may need surgical stabilization (fibular head ligament reconstruction)."],
            },
        }
    return {
        "Femoral shaft fracture": {
            "location": "Mid-shaft femur between greater and lesser trochanters.",
            "mechanism": "High-energy trauma (motor vehicle collision, fall from height), pathologic fracture in metastatic disease or osteoporosis.",
            "types": "Transverse, spiral, comminuted, segmental; open or closed.",
            "clinical": "Severe thigh pain, deformity, swelling, inability to bear weight. High risk of fat embolism and shock from blood loss.",
            "imaging": "Radiographs (AP, lateral) of entire femur including hip and knee; CT for complex patterns; pelvic radiograph to assess for associated injuries.",
            "treatment": "Closed: intramedullary nail fixation (gold standard). Open: urgent wound management, temporary fixation, staged definitive fixation.",
            "complications": "Hypovolemic shock, fat embolism, non-union, malunion, leg-length discrepancy, post-traumatic arthritis (if extends to knee).",
            "pearls": ["Femoral shaft fracture can lose 1-2 liters of blood into thigh compartment; assess for shock.", "Intramedullary nail is standard; allows early mobilization.", "Fat embolism risk; monitor respiratory status."],
        },
        "Tibial plateau fracture": {
            "location": "Proximal tibia articular surface; medial plateau, lateral plateau, or bicondylar.",
            "mechanism": "High-energy valgus or varus stress; axial load with rotational component in bicondylar fractures.",
            "types": "Schatzker classification (I-VI): simple medial or lateral (I-II), depressed (III-IV), split-depressed (V), complex bicondylar (VI).",
            "clinical": "Knee pain, swelling, hemarthrosis, difficulty bearing weight, valgus/varus deformity if displaced.",
            "imaging": "Radiographs (AP, lateral, 45° internal rotation); CT essential for fracture pattern, degree of depression, and surgical planning.",
            "treatment": "Non-displaced: conservative (immobilization, early motion if stable). Displaced: surgical fixation (screws, plates) to restore joint surface.",
            "complications": "Post-traumatic arthritis, residual knee instability, chronic effusion, compartment syndrome.",
            "pearls": ["Higher Schatzker grades have worse prognosis.", "CT crucial for surgical planning (shows depression amount, fragmentation).", "Often associated with meniscal tears; assess at time of surgery.", "Early range-of-motion critical for outcomes."],
        },
        "Patellar fracture": {
            "location": "Patella; upper pole, middle/body, lower pole; transverse (most common) or comminuted.",
            "mechanism": "Direct blow to front of knee (dashboard injury, fall), blunt trauma, or indirect (sudden quadriceps contraction during stumble).",
            "types": "Transverse (70%), comminuted (20%), vertical pole fractures (10%); displaced or non-displaced.",
            "clinical": "Anterior knee pain, swelling, hemarthrosis, inability to extend knee actively (disrupted extensor mechanism).",
            "imaging": "Radiographs (AP, lateral, axial views) show fracture location and displacement. CT for comminuted fractures.",
            "treatment": "Non-displaced with intact quadriceps mechanism: conservative (immobilization, early motion). Displaced or disrupted mechanism: surgical fixation (tension-band wiring, screws, plates).",
            "complications": "Extensor lag (weakness), patellofemoral arthritis, chronic pain, malunion.",
            "pearls": ["Always test for active knee extension to assess quadriceps mechanism.", "Comminuted fractures have worse prognosis.", "Tension-band technique is commonly used for transverse fractures.", "Early range-of-motion critical."],
        },
        "Distal femoral fracture": {
            "location": "Supracondylar region just above the femoral condyles (intercondylar area).",
            "mechanism": "High-energy trauma, falls in elderly with osteoporosis, motor vehicle collision.",
            "types": "Transverse, spiral, comminuted; often extends into knee joint (intra-articular).",
            "clinical": "Severe knee and lower thigh pain, swelling, deformity, hemarthrosis, inability to bear weight.",
            "imaging": "Radiographs (AP, lateral) of entire distal femur and knee; CT for complex patterns and joint involvement.",
            "treatment": "Operative fixation (screws, plates, intramedullary nail) standard for most; alignment critical to prevent malunion.",
            "complications": "Non-union, malunion with varus/valgus deformity, post-traumatic arthritis, stiffness.",
            "pearls": ["Careful reduction essential; malunion leads to functional deficit.", "Early motion is critical to prevent stiffness.", "Varus malunion is common and difficult to correct; proper alignment intraoperatively is key."],
        },
        "ACL avulsion fracture": {
            "location": "Intercondylar notch tibial attachment (more common than femoral insertion).",
            "mechanism": "Sudden deceleration, pivoting injury, or direct blow during knee flexion.",
            "types": "Tibial ACL avulsion (tibial spine fracture), femoral ACL avulsion (rare).",
            "clinical": "Acute knee pain, swelling, hemarthrosis, anterior drawer positive, pivot shift positive, Lachman positive.",
            "imaging": "Radiographs (AP, lateral) may show avulsed fragment; MRI for soft-tissue detail and associated injuries.",
            "treatment": "Surgical reduction and fixation (screws, suture anchor) to restore ACL function, especially in active patients.",
            "complications": "Chronic anterior knee instability, post-traumatic arthritis, knee effusion.",
            "pearls": ["ACL avulsion in skeletally immature patients requires surgical reattachment.", "Associated meniscal and collateral ligament injuries common.", "Early surgical fixation optimizes outcomes."],
        }
    }
