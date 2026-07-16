from app.airlines import decode_equipment, get_aircraft_display_name, get_airline_terminology


def test_american_terminology_is_sequence_specific():
    terminology = get_airline_terminology("american")
    assert terminology.singular == "Sequence"
    assert terminology.plural == "Sequences"
    assert terminology.recommended == "Recommended sequences"
    assert terminology.details == "Sequence details"
    assert terminology.view_original == "View original sequence"
    assert terminology.analyzed == "Sequences analyzed"


def test_confirmed_american_equipment_codes_decode_safely():
    expected = {
        "H319": "Airbus A319 CEO",
        "319W": "Airbus A319 CEO",
        "319S": "Airbus A319 CEO",
        "A320": "Airbus A320 CEO",
        "H205": "Airbus A320 CEO",
        "321K": "Airbus A321 CEO",
        "321T": "Airbus A321 CEO",
        "321R": "Airbus A321 CEO",
        "321N": "Airbus A321neo",
        "321E": "Airbus A321neo",
        "321X": "Airbus A321neo",
    }
    for code, aircraft in expected.items():
        definition = decode_equipment("american", code)
        assert definition.known is True
        assert definition.aircraft == aircraft
    assert decode_equipment("american", "321E").subfleet == "A321NX"
    assert get_aircraft_display_name("american", "321E") == "Airbus A321neo (321E)"


def test_unknown_equipment_code_falls_back_to_raw_code():
    definition = decode_equipment("american", "25")
    assert definition.known is False
    assert definition.raw_code == "25"
    assert definition.aircraft == "25"
    assert get_aircraft_display_name("american", "25") == "25"
