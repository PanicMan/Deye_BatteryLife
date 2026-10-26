import unittest
import logic

ZONE, MINROW = ["00:00", "06:00"], 30
REG = ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"]


class DecideSoc(unittest.TestCase):
    def d(self, cur, mx, user_min=15, cap=50):
        return logic.decide_soc(cur, mx, user_min, cap, 85, 95, 5)[0]

    def test_raise_when_not_full_enough(self):
        self.assertEqual(self.d(15, 70), 20)
        self.assertEqual(self.d(15, 84), 20)

    def test_keep_between_thresholds(self):
        self.assertEqual(self.d(20, 85), 20)
        self.assertEqual(self.d(20, 94), 20)

    def test_lower_when_full(self):
        self.assertEqual(self.d(25, 95), 20)
        self.assertEqual(self.d(25, 100), 20)

    def test_limits(self):
        self.assertEqual(self.d(15, 100), 15)   # user minimum
        self.assertEqual(self.d(50, 10), 50)    # cap
        self.assertEqual(self.d(48, 10), 50)    # clamped to cap, not 53
        self.assertEqual(self.d(10, 90, user_min=15), 15)  # below user minimum is pulled up
        self.assertEqual(self.d(25, 90, cap=20), 20)       # above cap is pulled down


class GridCharge(unittest.TestCase):
    def p(self, times):
        return logic.plan_grid_charge(times, ZONE, MINROW)[0]

    def test_regular_table(self):
        self.assertEqual(self.p(REG), [True, False, False, False, False, False])

    def test_odd_times_zero_length_rows(self):  # real fleet table: night row at 00:00, zero-length rows
        self.assertEqual(self.p(["04:00", "00:00", "03:55", "03:55", "03:55", "03:55"]), [False, True, False, False, False, False])

    def test_rotated_table(self):  # real fleet table: rotated row order
        self.assertEqual(self.p(["09:00", "13:00", "17:00", "21:00", "01:00", "05:00"]), [False] * 4 + [True, False])

    def test_row_starting_at_midnight(self):  # real fleet tables: first row starts at midnight
        self.assertEqual(self.p(["00:00", "05:00", "09:00", "13:00", "17:00", "21:00"])[0], True)
        self.assertEqual(self.p(["00:00", "06:00", "09:00", "13:00", "17:00", "21:00"])[0], True)

    def test_irregular_chaotic_table_is_left_alone(self):  # real fleet table: rows overlap
        flags, note = logic.plan_grid_charge(["00:00", "23:55", "00:00", "13:00", "17:00", "21:00"], ZONE, MINROW)
        self.assertIsNone(flags)
        self.assertIn("irregular", note)

    def test_no_night_row(self):  # real fleet table: only daytime rows
        flags, note = logic.plan_grid_charge(["03:00", "07:00", "09:00", "12:00", "16:00", "23:00"], ZONE, MINROW)
        self.assertIsNone(flags)
        self.assertIn("no row inside", note)

    def test_all_equal_times_invalid(self):
        self.assertIsNone(self.p(["01:00"] * 6))


class Build(unittest.TestCase):
    ITEMS = [{"time": t, "power": 10000, "voltage": 49, "soc": 15, "enableGridCharge": True,
              "enableGeneration": False, "enableSell": False} for t in REG]

    def test_flags_and_soc(self):
        new = logic.build_items(self.ITEMS, 20, [True] + [False] * 5)
        self.assertEqual([i["soc"] for i in new], [20] * 6)
        self.assertEqual([i["enableGridCharge"] for i in new], [True] + [False] * 5)
        self.assertEqual([i["time"] for i in new], REG)

    def test_flags_none_keeps_existing(self):
        new = logic.build_items(self.ITEMS, 20, None)
        self.assertTrue(all(i["enableGridCharge"] for i in new))

    def test_same_table(self):
        self.assertTrue(logic.same_table(self.ITEMS, logic.build_items(self.ITEMS, 15, None)))
        self.assertFalse(logic.same_table(self.ITEMS, logic.build_items(self.ITEMS, 20, None)))


class Ignored(unittest.TestCase):
    UNITS = [{"station_id": "1", "sn": "A"}, {"station_id": "1", "sn": "B"}, {"station_id": "2", "sn": "C"}]

    def test_ignored_device_disappears_but_station_is_marked(self):
        kept, stations, missing = logic.apply_ignored(self.UNITS, {"B": "no battery"})
        self.assertEqual([u["sn"] for u in kept], ["A", "C"])
        self.assertEqual(stations, {"1"})
        self.assertEqual(missing, [])

    def test_unknown_serial_is_reported(self):
        self.assertEqual(logic.apply_ignored(self.UNITS, {"Z": "gone"})[2], ["Z"])

    def test_nothing_ignored(self):
        kept, stations, missing = logic.apply_ignored(self.UNITS, {})
        self.assertEqual((len(kept), stations, missing), (3, set(), []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
