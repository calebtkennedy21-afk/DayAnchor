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
