# 🔋 Just Leverage Battery

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![GitHub Release](https://img.shields.io/github/v/release/JOUW_GITHUB_NAAM/ha-just-leverage-battery)](https://github.com/JOUW_GITHUB_NAAM/ha-just-leverage-battery/releases)

Een Home Assistant HACS-integratie voor slimme batterijhandel met de **Marstek Venus V3** op basis van **Zonneplan dynamische prijzen**.

---

## Functies

| Strategie | Omschrijving |
|---|---|
| **Arbitrage** | Automatisch laden in de goedkoopste uren, ontladen in de duurste uren |
| **UPS Stand-by** | Batterij blijft geladen als noodstroom reserve — geen handel |
| **Uit** | Integratie doet niets — Marstek regelt zichzelf |

**Veiligheidsregels (altijd actief):**
- Geen export naar het net (batterij ontlaadt alleen voor eigen verbruik)
- Minimale SOC instelbaar (UPS reserve — bijv. 20% altijd bewaard)
- Maximale SOC instelbaar (batterij niet overvol laden)

---

## Vereisten

Installeer eerst via HACS:

1. **[Marstek Local API](https://github.com/jaapp/ha-marstek-local-api)** — voor batterijbesturing
2. **[Zonneplan ONE](https://github.com/fsaris/home-assistant-zonneplan-one)** — voor uurprijzen met forecast

---

## Installatie

### Via HACS (aanbevolen)

1. Ga naar **HACS → Integraties → ⋮ → Aangepaste repositories**
2. Voeg toe: `https://github.com/Tinus-tech/ha-just-leverage-battery`
4. Categorie: **Integratie**
5. Zoek **Just Leverage Battery** en installeer
6. Herstart Home Assistant

### Handmatig

Kopieer de map `custom_components/just_leverage_battery` naar je HA `custom_components` map en herstart.

---

## Configuratie

1. Ga naar **Instellingen → Apparaten & Diensten → + Integratie toevoegen**
2. Zoek **Just Leverage Battery**
3. Vul in:

| Veld | Uitleg |
|---|---|
| **Marstek Device ID** | Kopieer uit Instellingen → Apparaten → jouw Marstek |
| **Prijssensor** | `sensor.zonneplan_current_electricity_tariff` |
| **Goedkope uren** | Hoeveel uur per dag laden (standaard: 4) |
| **Dure uren** | Hoeveel uur per dag ontladen (standaard: 4) |
| **Min SOC %** | Minimale batterijlading — bewaard als UPS reserve |
| **Max SOC %** | Maximale laadgrens |
| **Laadvermogen W** | Maximaal laadvermogen (bijv. 2000W) |
| **Ontlaadvermogen W** | Maximaal ontlaadvermogen (bijv. 2000W) |

---

## Entiteiten

Na installatie verschijnen deze entiteiten:

| Entiteit | Type | Omschrijving |
|---|---|---|
| `select.batterij_strategie_kiezer` | Select | Schakel tussen strategieën |
| `sensor.batterij_handelsstrategie` | Sensor | Actieve strategie |
| `sensor.batterij_laatste_actie` | Sensor | Wat de integratie nu doet |
| `sensor.batterij_beslissingsreden` | Sensor | Waarom die beslissing genomen is |

---

## Dashboard kaart (voorbeeld)

```yaml
type: entities
title: 🔋 Batterij Trader
entities:
  - entity: select.batterij_strategie_kiezer
  - entity: sensor.batterij_laatste_actie
  - entity: sensor.batterij_beslissingsreden
  - entity: sensor.marstek_battery_soc
  - entity: sensor.zonneplan_current_electricity_tariff
```

---

## Hoe werkt Arbitrage?

Elke minuut:
1. Haal Zonneplan uurprijzen op (forecast voor komende 24u)
2. Sorteer op prijs
3. Is het huidige uur in de goedkoopste N uur? → **Laden** via Passive Mode
4. Is het huidige uur in de duurste N uur? → **Ontladen** via Passive Mode
5. Anders → **Auto mode** (Marstek bestuurt zichzelf)

SOC-grenzen worden altijd gecontroleerd vóór elke actie.

---

## Marstek Device ID vinden

1. Ga naar **Instellingen → Apparaten & Diensten → Marstek Local API**
2. Klik op je batterij
3. Scroll naar beneden → kopieer de **Device ID** (lange hex string)

---

## Licentie

MIT — gebruik op eigen risico. Niet gelieerd aan Marstek of Zonneplan.
