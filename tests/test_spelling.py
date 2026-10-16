from litloop.spelling import to_us, variants


def test_to_us_british_to_american():
    input_text = "Aluminium fibre composites were characterised; colour, behaviour, modelling, sulphide, centre"
    expected = "aluminum fiber composites were characterized; color, behavior, modeling, sulfide, center"
    assert to_us(input_text) == expected


def test_variants():
    assert variants("aluminum") == ["aluminum", "aluminium"]
    assert variants("fibre") == ["fibre", "fiber"]
    assert variants("characterization") == ["characterization", "characterisation"]
    assert variants("SiC") == ["SiC"]
    assert variants("precise") == ["precise"]
    assert variants("analysis") == ["analysis"]


def test_to_us_consistency():
    british_sentence = "Aluminium fibre composites were characterised with modelling, colour, and behaviour at the centre."
    american_sentence = "Aluminum fiber composites were characterized with modeling, color, and behavior at the center."
    assert to_us(british_sentence) == to_us(american_sentence)
