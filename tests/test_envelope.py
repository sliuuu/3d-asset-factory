from hub.envelope import fit


def test_c3250_hunyuan_donor_fails_two_percent_rule():
    # Numbers from the 12 Oct Hunyuan3D 3.1 write-up: 0.98 x 0.46 x 0.57 normalised units vs the
    # HK06-1GEN_LB_01 envelope 8.9 x 3.9 m (height 3.6 m assumed).
    r = fit((0.98, 0.46, 0.57), {"L": 8.9, "W": 3.9, "H": 3.6, "unconfirmed": ["H"]})
    assert r["status"] == "fail"
    assert abs(r["scaled_extents_m"]["W"] - 4.18) < 0.01  # +7 %
    assert abs(r["residuals"]["W"] - 0.071) < 0.005
    assert r["scaled_extents_m"]["H"] > 5.1
    assert r["unconfirmed_axes"] == ["H"]


def test_proportional_donor_passes():
    r = fit((1.0, 0.44, 0.40), {"L": 8.9, "W": 3.9, "H": 3.6})
    assert r["status"] == "pass", r


def test_missing_envelope_is_skipped():
    assert fit((1, 1, 1), {})["status"] == "skipped"


def test_unconfirmed_axis_reports_but_does_not_fail():
    r = fit((1.0, 0.44, 0.6), {"L": 8.9, "W": 3.9, "H": 3.6, "unconfirmed": ["H"]})
    assert r["status"] == "pass"
    assert r["residuals"]["H"] > 0.4
