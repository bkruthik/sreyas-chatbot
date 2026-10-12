"""
modules/intent_engine.py
================================================================================
Production Intent Engine for SREYAS AI Assistant.

Pre-classifies user queries into 4 distinct categories before invoking RAG:
    Category A (OUT_OF_DOMAIN): Unrelated topics (pizza, pan shop, IPL, movies, etc.)
    Category B (CAMPUS_FAQ)   : Predefined campus life FAQs (cafeteria, gym, wifi, etc.)
    Category C (DYNAMIC_OPERATIONAL): Live updates (today's menu, holiday tomorrow, etc.)
    Category D (KB_QUERY)     : Genuine college knowledge queries -> RAG pipeline
================================================================================
"""

import re
from typing import Dict, Any, Optional
from modules.language import Localizer


class IntentEngine:
    """Pre-RAG intent classification and fast response dispatcher."""

    # ── Category A: Out of Domain (OOD) ──────────────────────────────────────
    _OOD_PATTERNS = [
        r"\b(pan\s*shop|baddi|gutkha|paan)\b",
        r"\b(pizza|burger|dominos|swiggy|zomato|kfc|mcdonalds|restaurant)\b",
        r"\b(ipl|cricket\s*score|match\s*score|t20|world\s*cup)\b",
        r"\b(movie|cinema|theatre|film|actor|actress|hero|heroine|box\s*office)\b",
        r"\b(whatsapp|facebook|instagram|snapchat|twitter|tiktok|youtube)\b",
        r"\b(amazon|flipkart|meesho|myntra|shopping)\b",
        r"\b(mobile\s*phone|iphone|samsung|redmi|realme|laptop\s*price)\b",
        r"\b(tourist|lakes?|charminar|golconda|tank\b|outside\s*visiting|hyderabad\s*places)\b",
        r"\b(weather|temperature|rain\s*today|climate)\b",
        r"\b(bike\s*model|royal\s*enfield|ktm|pulsar|car\s*brand|ferrari|bmw)\b",
        r"\b(crypto|bitcoin|stock\s*market|share\s*market)\b",
    ]

    # ── Category B: Campus Life FAQs (Predefined Concise Answers) ──────────────
    # Map normalized keys to internal lookup identifiers
    _CAMPUS_FAQ_MAP = {
        # Cafeteria / Food / Juice
        "cafeteria": "cafeteria", "canteen": "cafeteria", "food": "cafeteria",
        "juice": "juice_shop", "fruit juice": "juice_shop", "juice shop": "juice_shop",
        # Gym / Fitness
        "gym": "gym", "gymnasium": "gym", "fitness": "gym",
        # Parking & Two Wheelers
        "two wheeler": "two_wheelers", "two wheelers": "two_wheelers",
        "two-wheeler": "two_wheelers", "two-wheelers": "two_wheelers",
        "bike": "two_wheelers", "bikes": "two_wheelers",
        "parking": "parking", "vehicle parking": "parking", "bike parking": "parking",
        # Stationery / Xerox
        "stationery": "stationery", "stationery shop": "stationery",
        "xerox": "xerox", "photocopy": "xerox", "printout": "xerox",
        # Tech & Facilities
        "wifi": "wifi", "wi-fi": "wifi", "internet": "wifi",
        "drinking water": "drinking_water", "water": "drinking_water", "purified water": "drinking_water",
        "medical": "medical_room", "medical room": "medical_room", "first aid": "medical_room", "nurse": "medical_room",
        "atm": "atm", "cash machine": "atm",
        "lift": "lift", "elevator": "lift", "lifts": "lift",
        "washroom": "washrooms", "washrooms": "washrooms", "toilet": "washrooms", "restroom": "washrooms",
        "playground": "playground", "sports ground": "playground", "cricket ground": "playground",
        "cctv": "cctv", "security cameras": "cctv", "cameras": "cctv",
        # College timings
        "timing": "college_timings", "timings": "college_timings",
        "college timing": "college_timings", "college timings": "college_timings",
        "college time": "college_timings", "college hours": "college_timings",
        "working hours": "college_timings", "college open": "college_timings",
    }

    # ── Category C: Dynamic Operational Questions ─────────────────────────────
    _DYNAMIC_PATTERNS = [
        r"\b(menu|special|today'?s?\s*food|canteen\s*menu|food\s*menu)\b",
        r"\b(holiday|holiday\s*today|holiday\s*tomorrow|tomorrow\s*holiday)\b",
        r"\b(bus\s*delay|bus\s*delayed|bus\s*late|bus\s*status)\b",
        r"\b(timetable|schedule\s*today|class\s*today|today'?s?\s*timetable)\b",
        r"\b(exam\s*postponed|mid\s*postponed|postponed)\b",
        r"\b((company\s*visiting|placement\s*drive)\s*(today|tomorrow)|drive\s*today|drive\s*tomorrow)\b",
    ]

    # ── Predefined Campus FAQ Answers (Multilingual) ──────────────────────────
    CAMPUS_FAQ_ANSWERS: Dict[str, Dict[str, str]] = {
        "cafeteria": {
            "en": "Yes, Sreyas Institute has a clean canteen offering hygienic food and snacks at reasonable prices. A fresh fruit juice counter is also available inside the campus.",
            "te": "అవును, శ్రేయస్ ఇన్‌స్టిట్యూట్‌లో సరసమైన ధరలలో పరిశుభ్రమైన ఆహారం మరియు స్నాక్స్ అందించే కాంటీన్ ఉంది. క్యాంపస్‌లో తాజా ఫ్రూట్ జ్యూస్ కౌంటర్ కూడా అందుబాటులో ఉంది.",
            "hi": "हां, श्रेयस इंस्टीट्यूट में उचित मूल्य पर स्वच्छ भोजन और स्नैक्स प्रदान करने वाली एक साफ कैंटीन है। परिसर के भीतर एक ताजा फल का रस काउंटर भी उपलब्ध है।"
        },
        "juice_shop": {
            "en": "Yes, a fresh fruit juice and beverage shop is available inside the campus cafeteria.",
            "te": "అవును, క్యాంపస్ కాంటీన్‌లో తాజా ఫ్రూట్ జ్యూస్ మరియు పానీయాల దుకాణం అందుబాటులో ఉంది.",
            "hi": "हां, कैंपस कैंटीन के अंदर एक ताजा फलों का रस और पेय की दुकान उपलब्ध है।"
        },
        "gym": {
            "en": "Yes, a gymnasium facility is available on campus. Students can utilize it during non-academic leisure hours.",
            "te": "అవును, క్యాంపస్‌లో జిమ్‌నేషియం సదుపాయం ఉంది. విద్యార్థులు బోధనేతర సమయాల్లో దీనిని ఉపయోగించుకోవచ్చు.",
            "hi": "हां, कैंपस में जिम की सुविधा उपलब्ध है। छात्र गैर-शैक्षणिक खाली समय में इसका उपयोग कर सकते हैं।"
        },
        "two_wheelers": {
            "en": "Yes, students are allowed to bring two-wheelers to the campus. Designated student parking space is available.",
            "te": "అవును, విద్యార్థులు క్యాంపస్‌కు ద్విచక్ర వాహనాలను తీసుకురావడానికి అనుమతి ఉంది. ప్రత్యేక విద్యార్థి పార్కింగ్ స్థలం అందుబాటులో ఉంది.",
            "hi": "हां, छात्रों को कैंपस में दोपहिया वाहन लाने की अनुमति है। निर्दिष्ट छात्र पार्किंग स्थान उपलब्ध है।"
        },
        "parking": {
            "en": "Yes, spacious parking space is available for student two-wheelers and staff vehicles inside the campus at no extra fee.",
            "te": "అవును, ఎలాంటి అదనపు రుసుము లేకుండా క్యాంపస్‌లో విద్యార్థుల ద్విచక్ర వాహనాలు మరియు సిబ్బంది వాహనాల కోసం విశాలమైన పార్కింగ్ అందుబాటులో ఉంది.",
            "hi": "हां, कैंपस के अंदर छात्रों के दोपहिया वाहनों और स्टाफ के वाहनों के लिए बिना किसी अतिरिक्त शुल्क के पर्याप्त पार्किंग स्थान उपलब्ध है।"
        },
        "stationery": {
            "en": "Yes, a dedicated stationery shop is available on campus providing textbooks, notebooks, record sheets, and writing materials.",
            "te": "అవును, పాఠ్యపుస్తకాలు, నోట్‌బుక్‌లు, రికార్డు షీట్లు మరియు రైటింగ్ మెటీరియల్‌లను అందించే స్టేషనరీ షాప్ క్యాంపస్‌లో ఉంది.",
            "hi": "हां, कैंपस में एक समर्पित स्टेशनरी की दुकान उपलब्ध है जो पाठ्यपुस्तकें, नोटबुक, रिकॉर्ड शीट और लेखन सामग्री प्रदान करती है।"
        },
        "xerox": {
            "en": "Yes, a printing and Xerox facility is available inside the campus stationery shop for student project and document copying.",
            "te": "అవును, విద్యార్థుల ప్రాజెక్ట్ మరియు డాక్యుమెంట్ కాపీల కోసం క్యాంపస్ స్టేషనరీ షాప్‌లో ప్రింటింగ్ మరియు జిరాక్స్ సదుపాయం అందుబాటులో ఉంది.",
            "hi": "हां, छात्र प्रोजेक्ट और दस्तावेज फोटोकॉपी के लिए कैंपस स्टेशनरी दुकान के भीतर प्रिंटिंग और ज़ेरॉक्स सुविधा उपलब्ध है।"
        },
        "wifi": {
            "en": "Yes, secure high-speed Wi-Fi and 1 Gbps internet bandwidth are available across the campus, labs, and library.",
            "te": "అవును, క్యాంపస్, ల్యాబ్‌లు మరియు లైబ్రరీ అంతటా సురక్షితమైన హై-స్పీడ్ వైఫై మరియు 1 Gbps ఇంటర్నెట్ బ్యాండ్‌విడ్త్ అందుబాటులో ఉన్నాయి.",
            "hi": "हां, पूरे परिसर, प्रयोगशालाओं और पुस्तकालय में सुरक्षित हाई-स्पीड वाई-फाई और 1 Gbps इंटरनेट बैंडविड्थ उपलब्ध है।"
        },
        "drinking_water": {
            "en": "Yes, purified mineral drinking water (RO plants) and water coolers are installed on all floors across campus.",
            "te": "అవును, క్యాంపస్‌లోని అన్ని అంతస్తులలో శుద్ధి చేసిన మినరల్ డ్రింకింగ్ వాటర్ (RO ప్లాంట్లు) మరియు వాటర్ కూలర్లు ఏర్పాటు చేయబడ్డాయి.",
            "hi": "हां, कैंपस के सभी तलों पर शुद्ध मिनरल पेयजल (RO प्लांट) और वाटर कूलर लगाए गए हैं।"
        },
        "medical_room": {
            "en": "Yes, a dedicated medical room with a qualified full-time nurse and first-aid amenities is available on campus. Emergency tie-ups exist with nearby multi-specialty hospitals.",
            "te": "అవును, పూర్తి సమయం అర్హత కలిగిన నర్స్ మరియు ప్రథమ చికిత్స సౌకర్యాలతో ప్రత్యేక వైద్య గది క్యాంపస్‌లో అందుబాటులో ఉంది. సమీపంలోని ఆసుపత్రులతో అత్యవసర అనుబంధం ఉంది.",
            "hi": "हां, पूर्णकालिक योग्य नर्स और प्राथमिक चिकित्सा सुविधाओं के साथ एक समर्पित चिकित्सा कक्ष कैंपस में उपलब्ध है। नजदीकी अस्पतालों के साथ आपातकालीन व्यवस्था है।"
        },
        "atm": {
            "en": "Yes, ATM services and banking assistance are available close to the college main entrance.",
            "te": "అవును, కళాశాల ప్రధాన ద్వారానికి సమీపంలో ఎటిఎమ్ సేవలు మరియు బ్యాంకింగ్ సహాయం అందుబాటులో ఉన్నాయి.",
            "hi": "हां, कॉलेज के मुख्य द्वार के पास एटीएम सेवाएं और बैंकिंग सहायता उपलब्ध है।"
        },
        "lift": {
            "en": "Yes, elevator / lift facilities are operational in the main academic blocks for student and faculty convenience.",
            "te": "అవును, విద్యార్థులు మరియు అధ్యాపకుల సౌలభ్యం కోసం ప్రధాన అకాడమిక్ బ్లాక్‌లలో లిఫ్ట్ సదుపాయాలు అందుబాటులో ఉన్నాయి.",
            "hi": "हां, छात्रों और संकाय की सुविधा के लिए मुख्य शैक्षणिक ब्लॉकों में लिफ्ट सुविधाएं चालू हैं।"
        },
        "washrooms": {
            "en": "Yes, well-maintained, clean separate washrooms for boys, girls, and staff are present on all floors of every block.",
            "te": "అవును, ప్రతి బ్లాక్‌లోని అన్ని అంతస్తులలో అబ్బాయిలు, అమ్మాయిలు మరియు సిబ్బంది కోసం శుభ్రమైన ప్రత్యేక వాష్‌రూమ్‌లు ఉన్నాయి.",
            "hi": "हां, प्रत्येक ब्लॉक की सभी मंजिलों पर लड़कों, लड़कियों और कर्मचारियों के लिए साफ-सुथरे अलग शौचालय उपलब्ध हैं।"
        },
        "playground": {
            "en": "Yes, Sreyas has outdoor sports grounds for Cricket, Football, Volleyball, Basketball, and Throwball along with indoor games facilities.",
            "te": "అవును, శ్రేయస్‌లో క్రికెట్, ఫుట్‌బాల్, వాలీబాల్, బాస్కెట్‌బాల్ మరియు త్రోబాల్ కోసం అవుట్‌డోర్ స్పోర్ట్స్ మైదానాలు మరియు ఇండోర్ గేమ్స్ సౌకర్యాలు ఉన్నాయి.",
            "hi": "हां, श्रेयस में क्रिकेट, फुटबॉल, वॉलीबॉल, बास्केटबॉल और थ्रोबॉल के लिए आउटडोर खेल मैदान के साथ इनडोर खेल सुविधाएं हैं।"
        },
        "cctv": {
            "en": "Yes, 200+ CCTV cameras monitor all corridors, classrooms, labs, library, and campus grounds for 24x7 security and safety.",
            "te": "అవును, 24x7 భద్రత కోసం 200 కంటే ఎక్కువ CCTV కెమెరాలు కారిడార్లు, తరగతి గదులు, ల్యాబ్‌లు, లైబ్రరీ మరియు మైదానాలను పర్యవేక్షిస్తాయి.",
            "hi": "हां, 24x7 सुरक्षा के लिए 200+ सीसीटीवी कैमरे सभी गलियारों, कक्षाओं, प्रयोगशालाओं, पुस्तकालय और मैदानों की निगरानी करते हैं।"
        },
        "college_timings": {
            "en": "Official college working hours for academic classes are 09:15 AM to 04:00 PM (Monday to Saturday). The library remains open till 05:00 PM.",
            "te": "అకాడమిక్ తరగతులకు అధికారిక కళాశాల పనివేళలు ఉదయం 09:15 నుండి సాయంత్రం 04:00 వరకు (సోమవారం నుండి శనివారం వరకు). లైబ్రరీ సాయంత్రం 05:00 వరకు తెరిచి ఉంటుంది.",
            "hi": "अकादमिक कक्षाओं के लिए आधिकारिक कॉलेज का कार्य समय सुबह 09:15 बजे से शाम 04:00 बजे तक (सोमवार से शनिवार) है। पुस्तकालय शाम 05:00 बजे तक खुला रहता है।"
        },
    }

    @classmethod
    def classify(cls, query: str, language: str = "en") -> Dict[str, Any]:
        """
        Classifies `query` into one of:
            - Category A: OUT_OF_DOMAIN
            - Category B: CAMPUS_FAQ
            - Category C: DYNAMIC_OPERATIONAL
            - Category D: KB_QUERY (default for genuine college questions)

        Returns a dictionary formatted for app.py API response when A, B, or C matches,
        or `{"category_type": "D_KB_QUERY"}` when RAG should process the query.
        """
        q_clean = (query or "").strip()
        q_low = q_clean.lower()
        language = (language or "en").strip().lower()

        # 1. Category A: Out of Domain Check
        for pat in cls._OOD_PATTERNS:
            if re.search(pat, q_low):
                answer_text = Localizer.localized(
                    "ood_refusal", language,
                    "I'm designed to answer official questions related to Sreyas Institute of Engineering & Technology. "
                    "I can help with admissions, courses, fees, placements, academics, facilities, transport, scholarships, and other college-related information."
                )
                return {
                    "category_type": "A_OUT_OF_DOMAIN",
                    "answer": answer_text,
                    "show_form": False,
                    "confidence": 1.0,
                    "category": "GENERAL"
                }

        # 2. Category C: Dynamic Operational Check
        for pat in cls._DYNAMIC_PATTERNS:
            if re.search(pat, q_low):
                answer_text = Localizer.localized(
                    "dynamic_limitation", language,
                    "I'm sorry, I don't have access to live or day-to-day college operational updates such as today's menu, "
                    "tomorrow's holidays, or current bus/placement updates. Please contact the college office or check official college channels."
                )
                return {
                    "category_type": "C_DYNAMIC_OPERATIONAL",
                    "answer": answer_text,
                    "show_form": False,
                    "confidence": 1.0,
                    "category": "GENERAL"
                }

        # 3. Category B: Campus FAQ Check
        for keyword, faq_key in cls._CAMPUS_FAQ_MAP.items():
            if re.search(r"\b" + re.escape(keyword) + r"\b", q_low):
                faq_data = cls.CAMPUS_FAQ_ANSWERS.get(faq_key, {})
                ans_text = faq_data.get(language) or faq_data.get("en")
                if ans_text:
                    return {
                        "category_type": "B_CAMPUS_FAQ",
                        "answer": ans_text,
                        "show_form": False,
                        "confidence": 1.0,
                        "category": "FACILITIES"
                    }

        # 4. Category D: Knowledge Base Query (Default for College Queries)
        return {"category_type": "D_KB_QUERY"}
