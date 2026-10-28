# Deye BatteryLife

🇩🇪 Deutsch · [🇬🇧 English](README.md)

Tägliche dynamische Mindest-SoC-Anpassung (wie Victron BatteryLife) für Deye-Hybrid-Wechselrichter plus Gesundheitsbericht.

> **Hinweis:** Inoffizielles Projekt, steht in keiner Verbindung zu Deye. Das Skript schreibt Einstellungen auf echte
> Wechselrichter und Batterien. Nutzung auf eigene Verantwortung, ohne Gewährleistung (Lizenz: GPL-3.0). Immer zuerst mit `--dry-run` testen.

## Einrichtung (neuer Rechner / neue Mitarbeitende)

Voraussetzung: Python 3.10+ (nur Standardbibliothek, keine Pakete nötig).

1. `config.example.json` nach `config.json` und `secrets.example.json` nach `secrets.json` kopieren und ausfüllen.
   `secrets.json` enthält AppId/AppSecret und DeyeCloud-Login (Passwort als SHA256-Hex) und darf nie ins Repository.
   Unter Windows Zugriff einschränken: `icacls secrets.json /inheritance:r /grant:r "%USERNAME%:(F)"`.
2. `python test_logic.py` und `python test_health.py` (oder `python -m unittest discover`) ausführen.
3. Erst trocken testen: `python batterylife.py --dry-run --only <StationID>`, dann `python batterylife.py --health-only`.
4. Aufgaben im Task Scheduler anlegen: `powershell -File register_tasks.ps1`.

**Sicherheit:** Der API-Zugang steuert die Batterieeinstellungen aller Kundenanlagen. Nicht ins Repository, nicht in Chats oder Tickets, nicht weitergeben.
Im Repository liegen bewusst nur Code und Beispiel-Konfigurationen. Kundennamen, Stations-IDs, Verläufe und Sicherungen
(`config.json`, `state/`, `logs/`, `backups/`, `reports/`, `data/`, `runs.csv`, `Stationsliste.csv`, `Gesundheitsbericht.md`, `health_report.md`) stehen in `.gitignore`.

## Was passiert täglich (Task Scheduler)

| Aufgabe | Zeit | Aufruf |
|---|---|---|
| Deye BatteryLife | 18:00 | `batterylife.py` (Anpassung + Gesundheitsbericht) |
| Deye BatteryLife Retry | 18:45, 19:30 | `batterylife.py --retry` (nur Anlagen, die noch nicht erfolgreich durch sind) |

Der Rechner muss laufen und das Benutzerkonto, unter dem die Aufgaben registriert wurden, angemeldet sein.

**Schutz:** Stationen mit offener Störung ab Stufe 2 (`fault_block_min_level`) oder unplausibler Batteriespannung werden nicht angepasst, bis der Befund weg ist.

**Regel** (Maximum SoC der letzten 18 h): unter 85 % → Mindest-SoC +5, 85–94 % → bleibt, ab 95 % → −5.
Grenzen: Abschalt-SoC + 5 bis 50 %. Netzladung standardmäßig in **allen** TOU-Zeilen (`"grid_charge": "all"`), damit die Notladung des Akkus bei Zellunterspannung zu jeder Tageszeit funktioniert. Mit `"night"` nur in TOU-Zeilen, die ganz im Nachtfenster (00:00–06:00) liegen.

## Dateien

| Datei | Zweck |
|---|---|
| `Gesundheitsbericht.md` | **Hier nachsehen**: offene Befunde je Anlage (wird bei jedem Hauptlauf neu geschrieben, Archiv in `reports/`). Mit `"report_language": "en"` in `config.json` entsteht stattdessen `health_report.md` auf Englisch. |
| `config.json` | Schwellen, `live_allowlist`, `blacklist`, `health`, `health_acknowledged` |
| `runs.csv`, `logs/` | Verlauf und Tageslog jedes Laufs |
| `backups/<Datum>/<SN>.json` | TOU-Tabelle vor jeder Änderung (Rollback) |
| `state/<SN>.json` | Zustand je Wechselrichter |
| `secrets.json` | Zugangsdaten (nur für den Benutzer lesbar), nicht weitergeben |
| `Stationsliste.csv` | Stand des Flottenchecks vom 21.10.2026 (Stations-IDs) |

## Häufige Aufgaben

- **Anlage scharf schalten**: Stations-ID in `live_allowlist` eintragen. Alles andere läuft nur als Dry-Run (liest und protokolliert).
- **Anlage ausnehmen** (Kunde kümmert sich selbst): Stations-ID mit Grund unter `blacklist` eintragen. Es wird nichts gelesen oder geschrieben, nur der Gesundheitsbericht prüft weiter (nur Lesen von Verlaufsdaten).
- **Einzelnen Wechselrichter komplett ausblenden** (z. B. zweiter Wechselrichter ohne Akku in derselben Station): Seriennummer mit Grund unter `ignored_devices` eintragen. Er wird überall übersprungen (keine Anpassung, nicht im Gesundheitsbericht), die übrigen Wechselrichter der Station laufen normal.
- **Befund quittieren** (bekannt, soll nicht mehr als offen erscheinen): unter `health_acknowledged` eintragen:
  `"<StationID>": {"reason": "...", "rules": "all" | ["stuck_battery"], "until": "2026-12-31" | null}`.
  Mit `until` taucht der Befund nach Ablauf wieder als offen auf.
- **Zurückrollen**: Tabelle aus `backups/<Datum>/<SN>.json` per `strategy/dynamicControl` (nur `timeUseSettingItems`) zurückschreiben.
- **Manuell**: `python batterylife.py --dry-run` (nichts schreiben), `--only <StationID>,...` (nur diese Stationen), `--health-only` (nur Bericht, keine Befehle an Anlagen), `--no-health`.

## Neue Anlagen

Die Stationsliste wird bei jedem Lauf frisch aus der DeyeCloud geholt. Eine neue Anlage läuft automatisch im **Dry-Run** (liest und protokolliert, schreibt nichts) und steht 14 Tage unter "Neue Anlagen" im Gesundheitsbericht. Scharf wird sie erst, wenn ihre Stations-ID in `live_allowlist` steht. Nicht unterstützte Typen (nur `0_5411_1` ist freigegeben) werden übersprungen, Blacklist-Anlagen nie angefasst. Stationen ohne Wechselrichter stehen separat im Bericht.

## Wichtig

- Nicht gleichzeitig in der DeyeCloud-Weboberfläche an derselben Anlage arbeiten: Dann antwortet die API mit "command concurrent running"; das Skript wiederholt später.
- Ein Timeout (504) beim Schreiben heißt nicht, dass nichts angekommen ist; das Skript prüft deshalb immer per Rücklesen.
- Tests: `python test_logic.py`, `python test_health.py`.
