"""Mail notification for the health report: daily status mail plus an extra mail when new open findings appear.

Pure helpers (open_keys, new_keys, build) are unit tested; send() talks SMTP (standard library only).
"""
import smtplib
import ssl
from email.message import EmailMessage

import health

TEXT = {
    "de": {
        "daily": "Deye Tagesbericht: %d offen, %d neu", "alert": "Deye: %d neue Befunde (%s)",
        "new": "NEU seit dem letzten Bericht:", "open": "Offene Befunde:", "none": "Keine offenen Befunde.",
        "test_subject": "Deye BatteryLife: Testmail", "test_body": "Der Mailversand funktioniert.",
    },
    "en": {
        "daily": "Deye daily report: %d open, %d new", "alert": "Deye: %d new findings (%s)",
        "new": "NEW since the last report:", "open": "Open findings:", "none": "No open findings.",
        "test_subject": "Deye BatteryLife: test mail", "test_body": "Mail delivery works.",
    },
}


def open_keys(entries):
    """Identifiers of all open (not acknowledged) findings: 'station|serial|rule'."""
    return sorted({"%s|%s|%s" % (e["station_id"], e["sn"], f["rule"]) for e in entries for f in e["findings"] if not f["acked"]})


def new_keys(previous, current):
    return sorted(set(current) - set(previous))


def describe(entries, keys, lang):
    """One line per finding: 'Station: title - detail'."""
    titles = health.tr(lang)["rules"]
    wanted, lines = set(keys), []
    for e in entries:
        for f in e["findings"]:
            if "%s|%s|%s" % (e["station_id"], e["sn"], f["rule"]) in wanted:
                lines.append("- %s (%s): %s – %s" % (e["station"], e["station_id"], titles.get(f["rule"], f["rule"]), f["detail"]))
    return lines


def build(entries, current, new, report_text, lang, daily):
    """Returns (subject, body). Daily mails always contain the full report, alert mails the new findings first."""
    t = TEXT.get(lang, TEXT["de"])
    new_ids = {k.split("|")[0] for k in new}
    names = sorted({e["station"] for e in entries if e["station_id"] in new_ids})
    subject = t["daily"] % (len(current), len(new)) if daily else t["alert"] % (len(new), ", ".join(names[:3]) + (" …" if len(names) > 3 else ""))
    parts = []
    if new:
        parts += [t["new"]] + describe(entries, new, lang) + [""]
    parts += [t["open"]] + (describe(entries, current, lang) or [t["none"]]) + ["", "-" * 60, "", report_text]
    return subject, "\n".join(parts)


def send(smtp, to, subject, body):
    """smtp: {host, port, security: starttls|ssl, user, password, from}. Raises on any failure."""
    msg = EmailMessage()
    msg["From"] = smtp.get("from") or smtp["user"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body, charset="utf-8")
    ctx = ssl.create_default_context()
    if smtp.get("security", "starttls") == "ssl":
        with smtplib.SMTP_SSL(smtp["host"], int(smtp.get("port", 465)), timeout=30, context=ctx) as s:
            s.login(smtp["user"], smtp["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(smtp["host"], int(smtp.get("port", 587)), timeout=30) as s:
            s.starttls(context=ctx)
            s.login(smtp["user"], smtp["password"])
            s.send_message(msg)


def configured(smtp):
    return bool(smtp) and all(smtp.get(k) for k in ("host", "user", "password"))
