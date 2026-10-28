from vitalocal.ai_facts import MIN_HISTORY, deviation


def test_deviation_needs_history():
    assert deviation(50, [50] * (MIN_HISTORY - 1)) is None
    assert deviation(None, list(range(30))) is None


def test_deviation_positions_value_in_personal_range():
    hist = list(range(40, 61))  # 40..60, p25=45, p75=55, p10=42, p90=58
    assert deviation(50, hist)["position"] is None
    assert deviation(56, hist)["position"] == "slightly above usual"
    assert deviation(59, hist)["position"] == "clearly above usual"
    assert deviation(41, hist)["position"] == "clearly below usual"
    d = deviation(50, hist)
    assert d["usual_range_p25_p75"] == [45, 55] and d["median"] == 50


def test_small_differences_in_narrow_ranges_are_not_findings():
    hist = [13.0, 13.1, 13.2, 13.2, 13.3, 13.3, 13.4, 13.4, 13.2, 13.1, 13.3]
    assert deviation(12.9, hist, 1)["position"] == "clearly below usual"
    assert deviation(12.9, hist, 1, min_diff=0.5)["position"] is None
