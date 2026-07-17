# Haven

**De veilige thuishaven voor jullie Home Assistants.**

Haven laat één beheerder de Home Assistant-instanties van familie en vrienden
in de gaten houden — zonder VPN, port-forwards of gedeelde tokens. Elk huis
meert met een eigen sleutel aan bij de haven; wat van jou is, blijft van jou
(backups zijn end-to-end versleuteld en alleen door de eigenaar te openen).

| Add-on | Voor wie | Wat het doet |
|---|---|---|
| **Haven Hub** | de beheerder | Dashboard (ingress) met status, health, alerts, backups, updates, audit-log en acties per instantie |
| **Haven Agent** | familie/vrienden | Meert aan bij de hub over een beveiligde uitgaande WebSocket-tunnel; werkt door CGNAT, geen open poorten |

## Installatie

1. Home Assistant → **Instellingen → Add-ons → Add-on store**.
2. Rechtsboven ⋮ → **Repositories** → deze URL plakken → **Toevoegen**:
   ```
   https://github.com/kevinhaakma/haven-addons
   ```
3. De Haven-add-ons verschijnen onderaan in de store.

**Familie/vrienden** installeren alleen de **Haven Agent** en vullen bij
Configuratie de drie waarden in die ze van de hubbeheerder krijgen
(`hub_url` / `agent_id` / `agent_key` — uit de koppelcode-flow in de hub-UI).
Zodra de add-on start staat er "aangemeerd" in het log en verschijnt het huis
in de haven.

**De beheerder** installeert de **Haven Hub** en zet er een tunnel of
reverse proxy voor die `wss://jouw-domein/agent/ws` naar poort 8099
doorstuurt (bijv. de Cloudflared add-on). Alleen `/agent/ws` hoeft publiek;
de UI draait achter HA-ingress.

## Architectuur

```
familie-HA ──agent──► wss://haven.example.com/agent/ws ──tunnel──► hub (add-on)
                                                                    │ ingress (HA-auth)
                                                                    ▼ web-UI / API
```

- De agent maakt een **uitgaande** verbinding — bij familie is niets te
  configureren aan router of firewall.
- Tokens blijven op de eigen box: de agent gebruikt zijn lokale
  `SUPERVISOR_TOKEN`; de hub bewaart per agent alleen een sha256-hash van de
  agent-sleutel.
- De agent voert uitsluitend verzoeken uit binnen een vaste allowlist van
  Core- en Supervisor-endpoints (met method-check) — ook een gecompromitteerde
  hub kan geen willekeurige acties op de remote box uitvoeren.
- **Backups zijn van de eigenaar**: de agent versleutelt elke backup met een
  wachtwoord dat alleen op de eigen machine bestaat. De hub ziet dát er een
  backup is, maar kan hem niet openen.

Zie de DOCS van de afzonderlijke add-ons voor details, opties en beveiliging.

## Ontwikkeling

De bron van de waarheid is de `agent/`- en `hub/`-map in het
hoofdproject; de mappen hier worden gesynct met `sync.sh` (of het
robocopy-equivalent op Windows). Wijzigingen dus dáár maken, syncen, en deze
repository committen + pushen — iedereen die de repository heeft toegevoegd
krijgt de update via de normale add-on-store-updateflow.
