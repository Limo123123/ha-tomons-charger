# Tomons / Inateck EV Wallbox – Home Assistant Integration (HACS)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/Limo123123/ha-tomons-charger?include_prereleases)](https://github.com/Limo123123/ha-tomons-charger/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

Eine native Home Assistant Custom Integration zur Steuerung und Telemetrie-Auswertung von **Tomons / Inateck Wallboxen (EV Chargers)** über das Cloud-MQTT- und REST-Protokoll von Shenzhen Licheng Technology.

---

## 🚀 Features

* **Einfacher GUI Setup (Config Flow):**
  * Nur Eingabe von **E-Mail** und **Passwort** des Tomons-Accounts erforderlich.
  * Automatische Erkennung deiner registrierten Wallbox.
  * Kein manuelles Heraussuchen von MQTT-Brokern, UUIDs oder Tokens!
  * **Automatischer Token-Refresh:** Erneuert abgelaufene Sessions transparent im Hintergrund.
* **Live-Telemetrie:**
  * Netzspannung ($V$)
  * Ladestrom ($A$)
  * Wirkleistung ($W$)
  * Gehäusetemperatur ($^\circ C$)
  * Kabel- & Ladestatus (Standby / Verbunden / Lädt aktiv)
  * Lademodus (Sofortladen, Geplant, PV-Überschuss)
* **Energieerfassung & Home Assistant Energy Dashboard (Tag, Monat, Jahr):**
  * `sensor.<device>_gesamtenergie`: Kumulierte Gesamtenergie ($kWh$) mit `device_class: energy` und `state_class: total_increasing` für das offizielle **Home Assistant Energie-Dashboard**. Home Assistant berechnet daraus automatisch deine Verbräuche nach **Tag, Monat, Jahr** und Historie!
  * `sensor.<device>_ladeenergie_letzte_ladung`: Misst die exakt geladene Energie der aktuellen bzw. zuletzt abgeschlossenen Ladesitzung ($kWh$) in Echtzeit – selbst für kurze Ladungen unter 1 kWh.
* **Volle Steuerung & Automatisierung:**
  * **Ladestromgrenze (Slider):** Stufenlos von **6 A bis 32 A** (unterstützt 6, 8, 10, 13, 16, 20, 24, 32 A) über `number.<device>_ladestromgrenze`. Perfekt für **PV-Überschussladen**!
  * **Laden Start / Stopp (Schalter):** Manuelles oder automatisiertes Ein- und Ausschalten des Ladevorgangs über `switch.<device>_laden`.
  * **Telemetrie aktualisieren (Taster):** Manueller Aufweck- und Abfragebefehl (`0xCE01`) über `button.<device>_telemetrie_aktualisieren`.

---

## 📦 Installation via HACS

1. Öffne **Home Assistant** $\rightarrow$ **HACS** $\rightarrow$ **Integrationen**.
2. Klicke oben rechts auf das Drei-Punkte-Menü ($\vdots$) und wähle **Benutzerdefinierte Repositories** (*Custom repositories*).
3. Trage folgende URL ein:
   * **Repository:** `https://github.com/Limo123123/ha-tomons-charger`
   * **Typ:** `Integration`
4. Klicke auf **Hinzufügen** (*Add*).
5. Suche nach **Tomons / Inateck EV Wallbox**, klicke auf **Herunterladen** und starte Home Assistant neu.

---

## ⚙️ Einrichtung in Home Assistant

1. Gehe in Home Assistant zu **Einstellungen** $\rightarrow$ **Geräte & Dienste** $\rightarrow$ **Integration hinzufügen**.
2. Suche nach **Tomons / Inateck EV Wallbox**.
3. Gib deine **E-Mail-Adresse** und dein **Passwort** der Tomons-App ein.
4. Falls mehrere Wallboxen in deinem Account vorhanden sind, wähle das gewünschte Gerät aus.
5. Fertig! Alle Entitäten werden sofort erstellt und beginnen mit dem Live-Empfang.

---

## 📊 Entitäten-Übersicht

| Entität | Typ | Beschreibung | Einheit |
| :--- | :--- | :--- | :--- |
| `sensor.tomons_wallbox_wirkleistung` | Sensor | Aktuelle Ladeleistung | `W` |
| `sensor.tomons_wallbox_netzspannung` | Sensor | Aktuelle Netzspannung | `V` |
| `sensor.tomons_wallbox_ladestrom` | Sensor | Aktueller Ladestrom | `A` |
| `sensor.tomons_wallbox_gesamtenergie` | Sensor | Gesamter Zählerstand (für Energy Dashboard) | `kWh` |
| `sensor.tomons_wallbox_ladeenergie_letzte_ladung` | Sensor | Geladene Energie der letzten Ladesitzung | `kWh` |
| `sensor.tomons_wallbox_gehausetemperatur` | Sensor | Innentemperatur der Wallbox | `°C` |
| `sensor.tomons_wallbox_kabel_ladestatus` | Sensor | Textueller Status (Standby / Verbunden / Lädt aktiv) | - |
| `sensor.tomons_wallbox_lademodus` | Sensor | Modus (Sofortladen / Geplant / PV) | - |
| `binary_sensor.tomons_wallbox_fahrzeug_angesteckt` | Binary Sensor | `on` wenn Typ-2-Kabel im Auto steckt | - |
| `binary_sensor.tomons_wallbox_ladevorgang_aktiv` | Binary Sensor | `on` wenn Strom fließt | - |
| `binary_sensor.tomons_wallbox_verbindung` | Binary Sensor | `on` wenn Wallbox online | - |
| `number.tomons_wallbox_ladestromgrenze` | Number | Ladestrombegrenzung (6 A – 32 A) | `A` |
| `switch.tomons_wallbox_laden` | Switch | Ladevorgang starten / stoppen | - |
| `button.tomons_wallbox_telemetrie_aktualisieren` | Button | Manuelle Statusabfrage senden | - |

---

## ☀️ Praxis-Beispiele & Automationen

### 1. PV-Überschussladen (Dynamische Stromanpassung)
Passe den Ladestrom automatisch an den aktuellen Einspeiseüberschuss deiner Solaranlage an:

```yaml
alias: "Wallbox: PV-Überschussregelung"
trigger:
  - platform: numeric_state
    entity_id: sensor.solaranlage_uberschuss_watt
    above: 4100
    id: "increase"
  - platform: numeric_state
    entity_id: sensor.solaranlage_uberschuss_watt
    below: 1300
    id: "decrease"
condition:
  - condition: state
    entity_id: binary_sensor.tomons_wallbox_fahrzeug_angesteckt
    state: "on"
action:
  - choose:
      - conditions:
          - condition: trigger
            id: "increase"
        sequence:
          - service: number.set_value
            target:
              entity_id: number.tomons_wallbox_ladestromgrenze
            data:
              value: 10
      - conditions:
          - condition: trigger
            id: "decrease"
        sequence:
          - service: number.set_value
            target:
              entity_id: number.tomons_wallbox_ladestromgrenze
            data:
              value: 6
```

### 2. Benachrichtigung bei Ladeende
Erhalte eine Push-Mitteilung auf dein Smartphone mit den geladenen Kilowattstunden:

```yaml
alias: "Wallbox: Benachrichtigung Ladeende"
trigger:
  - platform: state
    entity_id: binary_sensor.tomons_wallbox_ladevorgang_aktiv
    from: "on"
    to: "off"
action:
  - service: notify.notify
    data:
      title: "⚡ Ladevorgang beendet"
      message: >
        Das Auto ist fertig geladen.
        Geladene Energie dieser Sitzung: {{ states('sensor.tomons_wallbox_ladeenergie_letzte_ladung') }} kWh.
```

### 3. Dashboard Lovelace Karte

```yaml
type: entities
title: Tomons Wallbox
entities:
  - entity: switch.tomons_wallbox_laden
  - entity: number.tomons_wallbox_ladestromgrenze
  - entity: sensor.tomons_wallbox_kabel_ladestatus
  - entity: sensor.tomons_wallbox_wirkleistung
  - entity: sensor.tomons_wallbox_ladestrom
  - entity: sensor.tomons_wallbox_netzspannung
  - entity: sensor.tomons_wallbox_ladeenergie_letzte_ladung
  - entity: sensor.tomons_wallbox_gesamtenergie
  - entity: sensor.tomons_wallbox_gehausetemperatur
  - entity: button.tomons_wallbox_telemetrie_aktualisieren
```

---

## 🔒 Datenschutz & Sicherheit
Diese Integration verwendet TLS-Verbindungen zur REST-API von Licheng Technology und greift über gesicherte MQTT-Credentials direkt auf den Broker zu. Weder Passwörter noch Tokens werden an Dritte übertragen.

---

## 📄 Lizenz
MIT License © 2026.
Dieses Projekt steht in keiner offiziellen Verbindung zu Shenzhen Licheng Technology Co., Ltd. oder Inateck / Tomons.
