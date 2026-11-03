from lostfound.textnorm import extract


def test_reads_detailed_report():
    a = extract("Lost my black Woodland wallet, leather, torn. Near the canteen.").attrs
    assert a.category == "wallet"
    assert a.brand == "Woodland"
    assert a.primary_color == "black"
    assert a.material == "leather"
    assert a.damages == ["torn"]


def test_reads_model_number():
    a = extract("Found a blue iPhone 13 with a cracked screen near the library").attrs
    assert a.category == "phone"
    assert a.brand == "iPhone"
    assert a.model == "13"
    assert a.primary_color == "blue"
    assert "cracked" in a.damages


def test_terse_report_leaves_fields_unstated():
    a = extract("blue bottle").attrs
    assert a.category == "bottle"
    assert a.primary_color == "blue"
    assert a.brand is None and a.material is None
    # missing evidence must not be reported as a mismatch
    assert a.completeness() < 0.5


def test_colour_alias_and_multicolour():
    a = extract("dark blue sipper with a white lid").attrs
    assert a.category == "bottle"
    assert a.primary_color == "navy"
    assert a.secondary_color == "white"


def test_serial_is_captured():
    a = extract("Found an HP laptop, tag reads SIT-2024-1234").attrs
    assert a.category == "laptop"
    assert a.brand == "HP"
    assert a.visible_text == ["SIT-2024-1234"]
