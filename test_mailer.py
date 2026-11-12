import unittest
from unittest import mock

import mailer

ENTRIES = [
    {"station": "Station A", "station_id": "1", "sn": "S1", "findings": [
        {"rule": "stuck_battery", "detail": "SoC 70%", "since": "2026-11-12", "acked": False, "note": ""},
        {"rule": "offline", "detail": "alt", "since": "2026-11-01", "acked": True, "note": "bekannt"}]},
    {"station": "Station B", "station_id": "2", "sn": "S2", "findings": []},
    {"station": "Station C", "station_id": "3", "sn": "S3", "findings": [
        {"rule": "active_alert", "detail": "SoC 4%", "since": "2026-11-12", "acked": False, "note": ""}]},
]
SMTP = {"host": "smtp.example.org", "port": 587, "security": "starttls", "user": "u@example.org", "password": "pw", "from": "from@example.org"}


class Keys(unittest.TestCase):
    def test_acknowledged_findings_are_not_open(self):
        self.assertEqual(mailer.open_keys(ENTRIES), ["1|S1|stuck_battery", "3|S3|active_alert"])

    def test_new_keys(self):
        self.assertEqual(mailer.new_keys(["1|S1|stuck_battery"], mailer.open_keys(ENTRIES)), ["3|S3|active_alert"])
        self.assertEqual(mailer.new_keys(mailer.open_keys(ENTRIES), mailer.open_keys(ENTRIES)), [])


class Build(unittest.TestCase):
    def test_daily_mail_has_full_report_and_counts(self):
        cur = mailer.open_keys(ENTRIES)
        subject, body = mailer.build(ENTRIES, cur, ["3|S3|active_alert"], "REPORT TEXT", "de", True)
        self.assertEqual(subject, "Deye Tagesbericht: 2 offen, 1 neu")
        self.assertIn("NEU seit dem letzten Bericht:", body)
        self.assertIn("Station C (3): Aktive Störung/Warnung", body)
        self.assertTrue(body.rstrip().endswith("REPORT TEXT"))

    def test_alert_mail_names_the_stations(self):
        subject, body = mailer.build(ENTRIES, mailer.open_keys(ENTRIES), ["3|S3|active_alert"], "R", "de", False)
        self.assertEqual(subject, "Deye: 1 neue Befunde (Station C)")

    def test_english(self):
        subject, body = mailer.build(ENTRIES, [], [], "R", "en", True)
        self.assertEqual(subject, "Deye daily report: 0 open, 0 new")
        self.assertIn("No open findings.", body)

    def test_many_stations_are_shortened(self):
        entries = [{"station": "S%d" % i, "station_id": str(i), "sn": "x", "findings": [
            {"rule": "offline", "detail": "d", "since": "2026-11-12", "acked": False, "note": ""}]} for i in range(5)]
        keys = mailer.open_keys(entries)
        subject, _ = mailer.build(entries, keys, keys, "R", "de", False)
        self.assertIn("…", subject)


class Send(unittest.TestCase):
    def test_starttls_login_and_message(self):
        with mock.patch("mailer.smtplib.SMTP") as smtp_cls:
            conn = smtp_cls.return_value.__enter__.return_value
            mailer.send(SMTP, ["info@example.org", "b@example.org"], "Betreff ä", "Körper")
        smtp_cls.assert_called_once_with("smtp.example.org", 587, timeout=30)
        conn.starttls.assert_called_once()
        conn.login.assert_called_once_with("u@example.org", "pw")
        msg = conn.send_message.call_args[0][0]
        self.assertEqual((msg["To"], msg["From"], msg["Subject"]), ("info@example.org, b@example.org", "from@example.org", "Betreff ä"))
        self.assertIn("Körper", msg.get_content())

    def test_ssl_variant(self):
        with mock.patch("mailer.smtplib.SMTP_SSL") as ssl_cls:
            mailer.send({**SMTP, "security": "ssl", "port": 465}, ["a@example.org"], "s", "b")
        self.assertEqual(ssl_cls.call_args[0][:2], ("smtp.example.org", 465))

    def test_failure_is_raised(self):
        with mock.patch("mailer.smtplib.SMTP", side_effect=OSError("down")):
            with self.assertRaises(OSError):
                mailer.send(SMTP, ["a@example.org"], "s", "b")

    def test_configured(self):
        self.assertTrue(mailer.configured(SMTP))
        self.assertFalse(mailer.configured({**SMTP, "password": ""}))
        self.assertFalse(mailer.configured(None))


if __name__ == "__main__":
    unittest.main()
