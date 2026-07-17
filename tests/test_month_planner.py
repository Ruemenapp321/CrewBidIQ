from app.month_planner import build_month_plan


def item(pairing, match_class, operations, pay="20:00"):
    return {"pairing": pairing, "eligible": True, "match_class": match_class, "operations": operations, "total_pay": pay}


def test_month_pools_use_occurrences_not_only_unique_trip_ids():
    plan = build_month_plan({}, [item("100", "exact", 5), item("200", "exact", 3), item("300", "strong", 4)])
    assert plan["pools"]["primary"]["unique_trip_count"] == 2
    assert plan["pools"]["primary"]["occurrence_count"] == 8
    assert plan["pools"]["secondary"]["occurrence_count"] == 4


def test_month_plan_warns_when_primary_pool_is_too_small():
    plan = build_month_plan({"target_credit_min": 90, "target_credit_max": 90}, [item("100", "exact", 2, "15:00"), item("200", "partial", 2, "15:00")])
    assert plan["estimated_trips_needed"] == 6
    assert any("Primary Pool" in warning for warning in plan["warnings"])
    assert plan["monthly_credit_feasibility"] == "Pool too small"


def test_ineligible_near_matches_never_enter_month_pools():
    near = {**item("NEAR", "near", 20), "eligible": False}
    plan = build_month_plan({}, [near, item("EXACT", "exact", 1)])
    assert plan["eligible_occurrence_count"] == 1
