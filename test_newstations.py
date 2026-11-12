import json
import pathlib
import tempfile
import unittest

import batterylife as b

CFG = {"health": {"new_station_days": 14}, "blacklist": {"3": "x"}, "live_allowlist": ["2"], "supported_products": ["P"], "report_language": "de"}
STATIONS = {"1": "A", "2": "B", "3": "C", "4": "D"}
UNITS = [{"station_id": s, "product": "P"} for s in ("1", "2", "3")] + [{"station_id": "4", "product": "OTHER"}]


class NewStations(unittest.TestCase):
    def setUp(self):
        self.old = b.ROOT
        b.ROOT = pathlib.Path(tempfile.mkdtemp())
        (b.ROOT / "state").mkdir()
        (b.ROOT / "state" / "_known_stations.json").write_text(json.dumps({s: "2026-11-10" for s in STATIONS}))

    def tearDown(self):
        b.ROOT = self.old

    def test_note_depends_on_mode(self):
        new, _ = b.track_new_stations(STATIONS, UNITS, CFG, "2026-11-12", False)
        notes = {n["id"]: n["note"] for n in new}
        self.assertIn("live", notes["2"])
        self.assertIn("Dry-Run", notes["1"])
        self.assertEqual(notes["3"], "Blacklist")
        self.assertIn("nicht unterstützt", notes["4"])

    def test_old_stations_drop_out(self):
        new, _ = b.track_new_stations(STATIONS, UNITS, CFG, "2026-11-30", False)
        self.assertEqual(new, [])


if __name__ == "__main__":
    unittest.main()
