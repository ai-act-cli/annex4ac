from importlib.resources import files


def test_srgb_icc_present():
    icc = files("annex4ac").joinpath("resources/sRGB.icc")
    assert icc.is_file()
    assert len(icc.read_bytes()) > 0
