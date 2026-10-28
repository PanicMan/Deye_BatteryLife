import unittest
import health

H = {"window_hours": 18, "stuck_min_span_hours": 12, "stuck_max_range": 1, "stuck_above_floor": 3, "stuck_max_soc": 95,
     "stuck_min_load_w": 150, "stuck_min_pv_w": 300, "stuck_max_battery_w": 100, "stale_minutes": 60, "offline_hours": 24,
     "soc_mismatch": 10, "busy_days": 2, "assumed_floor_soc": 15}
NOW = 1_800_000_000


def inp(**kw):
    series = kw.pop("series", [(NOW - 18 * 3600 + k * 340, 70) for k in range(190)])
    base = dict(now=NOW, online=True, station_status="NORMAL", collection_time=NOW - 120, series=series, pv=[1000.0] * 190,
                load=[900.0] * 190, battery=[10.0] * 190, soc_now=70, bms_soc=70, floor=15, busy_streak=0)
    base.update(kw)
    return base


def rules(**kw):
    return [r for r, _ in health.evaluate(inp(**kw), H)]


class Rules(unittest.TestCase):
    def test_battery_stuck_at_70(self):
        self.assertEqual(rules(), ["stuck_battery"])

    def test_moving_battery_is_fine(self):
        s = [(NOW - 18 * 3600 + k * 340, 20 + k % 60) for k in range(190)]
        self.assertEqual(rules(series=s, battery=[800.0] * 190), [])

    def test_flat_at_floor_is_normal(self):  # real fleet cases: battery idle at its floor
        s = [(NOW - 18 * 3600 + k * 340, 15) for k in range(190)]
        self.assertEqual(rules(series=s, soc_now=15, bms_soc=15), [])

    def test_full_battery_is_normal(self):
        s = [(NOW - 18 * 3600 + k * 340, 100) for k in range(190)]
        self.assertEqual(rules(series=s, soc_now=100, bms_soc=100), [])

    def test_no_load_no_pv_is_not_flagged(self):
        self.assertEqual(rules(pv=[0.0] * 190, load=[20.0] * 190), [])

    def test_short_window_is_not_flagged(self):
        s = [(NOW - 3 * 3600 + k * 340, 70) for k in range(30)]
        self.assertEqual(rules(series=s), [])

    def test_offline_long(self):
        self.assertEqual(rules(online=False, collection_time=NOW - 30 * 3600), ["offline"])

    def test_offline_short_is_tolerated(self):
        self.assertEqual(rules(online=False, collection_time=NOW - 3 * 3600), [])

    def test_stale(self):
        self.assertEqual(rules(collection_time=NOW - 3 * 3600), ["stale_data"])

    def test_commands_streak(self):
        self.assertIn("commands", rules(busy_streak=2, series=[(NOW - 18 * 3600 + k * 340, 20 + k % 60) for k in range(190)]))
        self.assertNotIn("commands", rules(busy_streak=1))

    def test_soc_mismatch(self):
        s = [(NOW - 18 * 3600 + k * 340, 20 + k % 60) for k in range(190)]
        self.assertEqual(rules(series=s, battery=[800.0] * 190, soc_now=50, bms_soc=30), ["soc_mismatch"])

    def test_no_history(self):
        self.assertEqual(rules(series=[]), ["no_data"])

    def test_station_status(self):
        s = [(NOW - 18 * 3600 + k * 340, 20 + k % 60) for k in range(190)]
        self.assertEqual(rules(series=s, battery=[800.0] * 190, station_status="PARTIAL_OFFLINE"), ["station_status"])


class AlertsAndVoltage(unittest.TestCase):
    MOVING = [(NOW - 18 * 3600 + k * 340, 20 + k % 60) for k in range(190)]
    ALERT = {"name": "F56 DC_VoltLow_Fault", "level": 2, "since": "27.10. 16:39"}

    def r(self, **kw):
        return rules(series=self.MOVING, battery=[800.0] * 190, **kw)

    def test_active_alert_is_reported_with_name(self):
        found = health.evaluate(inp(series=self.MOVING, battery=[800.0] * 190, alerts=[self.ALERT]), H)
        self.assertEqual([r for r, _ in found], ["active_alert"])
        self.assertIn("F56 DC_VoltLow_Fault", found[0][1])

    def test_disconnected_battery_fault_voltage_and_frozen_soc(self):  # real case: 7.5 V, F56, SoC frozen at 80 %
        self.assertEqual(set(self.r(alerts=[self.ALERT], battery_voltage=7.5, device_state=2)), {"active_alert", "battery_voltage"})

    def test_alarm_state_without_alert_list(self):
        self.assertEqual(self.r(device_state=2), ["device_state"])

    def test_normal_state_and_voltage(self):
        self.assertEqual(self.r(device_state=1, battery_voltage=51.2, alerts=[]), [])

    def test_low_battery_voltage_alone(self):
        self.assertEqual(self.r(battery_voltage=7.5), ["battery_voltage"])

    def test_alerts_ignored_when_offline(self):
        self.assertEqual(rules(online=False, collection_time=NOW - 30 * 3600, alerts=[self.ALERT]), ["offline"])


class Languages(unittest.TestCase):
    def test_every_german_text_has_an_english_one(self):
        self.assertEqual(set(health.T["de"]), set(health.T["en"]))
        self.assertEqual(set(health.T["de"]["rules"]), set(health.T["en"]["rules"]))

    def test_detail_language(self):
        de = health.evaluate(inp(), H)[0][1]
        en = health.evaluate(inp(), H, "en")[0][1]
        self.assertIn("konstant", de)
        self.assertIn("constant", en)

    def test_render_english(self):
        entries = [{"station": "S", "station_id": "1", "sn": "x", "findings": [
            {"rule": "stuck_battery", "detail": "d", "since": "2026-10-22", "acked": False, "note": ""}]}]
        meta = {"time": "t", "units": 1, "stations": 1, "health": H, "lang": "en", "new": [], "no_inverter": []}
        text = health.render(entries, meta, {}, "2026-10-22")
        self.assertIn("# Deye station health report", text)
        self.assertIn("Battery not moving", text)
        self.assertNotIn("Offen", text)

    def test_unknown_language_falls_back_to_german(self):
        self.assertIs(health.tr("fr"), health.T["de"])


class Acks(unittest.TestCase):
    ACKS = {"1": {"reason": "ok", "rules": ["stuck_battery"], "until": None}, "2": {"reason": "hold", "rules": "all", "until": "2026-10-25"}}

    def test_rule_specific(self):
        self.assertTrue(health.ack_for(self.ACKS, "1", "stuck_battery", "2026-10-22")[0])
        self.assertFalse(health.ack_for(self.ACKS, "1", "offline", "2026-10-22")[0])

    def test_expiry(self):
        self.assertTrue(health.ack_for(self.ACKS, "2", "offline", "2026-10-25")[0])
        acked, note = health.ack_for(self.ACKS, "2", "offline", "2026-10-26")
        self.assertFalse(acked)
        self.assertIn("abgelaufen", note)

    def test_unknown_station(self):
        self.assertEqual(health.ack_for(self.ACKS, "9", "offline", "2026-10-22"), (False, ""))


if __name__ == "__main__":
    unittest.main()
