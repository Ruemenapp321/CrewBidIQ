from app.parsers import southwest

SAMPLE = """XA28  MO          PILOTS      REPORT AT 7:05            EFFECTIVE Aug 10-Aug 31
  DAY       FLT  EQP DEPARTS  ARRIVES    BLK  BLK DUTY  CR   LAYOVER     M
  MO        2468 700 LAX 0805 OAK 0925   1:20           1.50
  MO        3001 700 OAK 1005 SAN 1130   1:25           1.60
  MO        3001 700 SAN 1210 SAT 1455   2:45           3.20 SAT 15:05
                                             5:30  8:20 6.30
                 REPORT AT 6:30
  TU        4646 700 SAT 0700 DEN 0910*  2:10           2.50
  TU         804 800 DEN 1005 RNO 1220   2:15           2.60
  TU         804 800 RNO 1300 LAX 1435   1:35           1.80
                                             6:00  8:35 6.90
Trip Credit 13.20 BLK HRS 11:30 No. Legs 6 TAFB 32:00 Position
--------------------------------------------------------------------------------------------
"""


def test_detects_southwest():
    assert southwest.detect(SAMPLE) >= .6


def test_parses_southwest_pairing():
    rows = southwest.parse(SAMPLE)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "XA28"
    assert len(row["legs"]) == 6
    assert row["layovers"][0]["city"] == "SAT"
    assert row["layovers"][0]["duration"] == "15:05"
    assert row["credit"] == "13.20"
    assert row["raw_trip_credit_label"] == "Trip Credit"
    assert row["pairing_tfp"] == "13.20"
    assert row["tfp_per_duty_period"] == "6.60"
    assert row["tfp_per_day_away"] == "9.90"
    assert row["tafb"] == "32:00"
    assert row["release"] == "1435"
