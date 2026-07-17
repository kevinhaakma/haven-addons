# HA Fleet Hub

Centraal beheerpaneel voor de Home Assistant-instanties van jezelf, familie en
vrienden — status, health, backups, updates en meer, allemaal op één plek.

## Wat doet deze add-on

De hub houdt een lijst instanties bij: eigen instanties gekoppeld via een
directe URL + long-lived token (LAN, Tailscale, Nabu Casa), en instanties bij
familie/vrienden die verbinden via de **HA Fleet Agent** add-on (uitgaande
tunnel, geen open poorten nodig aan hun kant).

Via de web-UI (ingress) zie je in één overzicht:

- Online/offline-status, core-versie, beschikbare updates en laatste backup
  per instantie.
- **Alerts** — automatische signalering wanneer een instantie te lang offline
  is, de laatste backup te oud is, een update klaarstaat, er veel dode
  entities zijn, de schijf bijna vol raakt, of de supervisor zelf
  problemen meldt. Optioneel een pushnotificatie via een `notify.*`-service.
- **Audit-log** — een chronologisch overzicht van alle acties die via de hub
  zijn uitgevoerd (backups, updates, herstarts, integraties herladen,
  instanties toevoegen/verwijderen, sleutel-rotaties, agent-verbindingen).
- **Instellingen** — alerts aan/uit, welke instantie notificaties verstuurt,
  een wekelijkse geplande volledige backup, en de drempelwaarden voor alerts.
- Acties per instantie: backup maken, updates installeren, herstarten, en
  losse integraties (config-entries) herladen zonder een volledige herstart.
- Host-metrics (schijfruimte) en een gezondheidsindicator per instantie.

## Opties

| Optie | Omschrijving |
|---|---|
| `public_hub_url` | Het publieke WebSocket-adres waarop agents deze hub bereiken (bijv. `wss://fleet.example.com/agent/ws`). Wordt gebruikt in de koppelcodes die de UI genereert. |
| `poll_interval` | Hoe vaak (in seconden) de hub alle instanties controleert. Standaard 300. |

Instanties, agent-koppelingen en de overige instellingen worden beheerd via de
web-UI zelf (ingress) en opgeslagen in de add-on-data (`/config` bij deze
mapping) — die overleven een update of herinstallatie van de add-on.

Voor het publieke agent-pad heb je een tunnel of reverse proxy nodig die
`wss://jouw-domein/agent/ws` doorstuurt naar poort 8099 van deze add-on
(bijv. de Cloudflared add-on met een `additional_hosts`-regel). Alleen
`/agent/ws` hoeft publiek; de UI en API weigeren alles buiten ingress/loopback.

## Koppelen

**Eigen instanties (directe URL):** voeg ze toe via de UI met een URL (LAN-IP,
Tailscale-adres of Nabu Casa) en een long-lived access token van die
instantie.

**Instanties bij familie/vrienden (agent):**

1. Hub-UI -> **Instantie** -> *Agent-koppeling* -> naam invullen ->
   **Koppelcode maken**.
2. Laat hen de **HA Fleet Agent** add-on installeren (zie de add-on-repository
   in `repo/`) en de gegenereerde `hub_url` / `agent_id` / `agent_key` invullen.
3. Zodra hun add-on start, verschijnt de instantie in de hub als "tunnel
   verbonden" — geen verdere actie nodig.
4. Sleutel kwijt of gelekt? **Instantie -> Sleutel roteren** maakt de oude
   sleutel ongeldig en levert direct de nieuwe add-on-configuratie op.

## Backups en privacy

Backups op **agent-instanties** worden end-to-end versleuteld: de agent
(v1.3.0+) zet er lokaal een wachtwoord op dat alleen op die machine bestaat.
De hub kan backups aanvragen en zien dát ze er zijn (slotje in de Backups-tab),
maar kan ze niet openen — alleen de eigenaar van de instantie zelf kan
herstellen. Dat is bewust: de hubbeheerder hoort niet bij de inhoud
(wachtwoorden, tokens, camera's) van andermans Home Assistant te kunnen.

## Toegang

De web-UI en API zijn alleen bereikbaar via HA-ingress (of loopback tijdens
lokale ontwikkeling) — er wordt niets publiek blootgesteld vanaf deze add-on
zelf. Het enige publieke pad in de architectuur is het WebSocket-eindpunt
waar agents op verbinden, en dat loopt via een aparte tunnel (bijv.
Cloudflared) voor de hub.
