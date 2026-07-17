# HA Fleet Agent

Koppelt deze Home Assistant-instantie aan een HA Fleet Hub, zodat die op afstand
kan beheren — zonder dat er hier een poort open hoeft te staan.

## Wat doet deze add-on

De agent legt een **uitgaande** WebSocket-verbinding naar de hub (`wss://.../agent/ws`)
en houdt die open. Werkt ook door CGNAT of achter een router zonder port-forward,
omdat de agent altijd naar buiten belt — nooit andersom.

Zodra de hub iets vraagt (status, health, backup maken, update installeren,
herstarten, een integratie herladen, …), stuurt hij dat verzoek over de bestaande
tunnel. De agent voert het lokaal uit met zijn eigen `SUPERVISOR_TOKEN` — dat token
verlaat dit systeem nooit — en stuurt het resultaat terug.

**Wat de agent NIET doet**: geen inkomende poorten, geen extra token dat de hub
hoeft te bewaren buiten de agent-sleutel, en geen toegang buiten een vaste
lijst toegestane Core- en Supervisor-endpoints (backups, updates, herstart,
logs, host-/resolutie-info, config-entries). Verzoeken buiten die lijst worden
geweigerd.

## Opties

| Optie | Omschrijving |
|---|---|
| `hub_url` | WebSocket-URL van de hub, bijv. `wss://fleet.kvn.frl/agent/ws`. Van de hubbeheerder gekregen. |
| `agent_id` | Unieke naam voor deze instantie, bijv. `mama` of `broer`. Van de hubbeheerder gekregen. |
| `agent_key` | Geheime sleutel die bij `agent_id` hoort. Van de hubbeheerder gekregen. |

Alle drie de velden komen uit de **koppelcode-flow** in de hub-UI (Instantie ->
Agent-koppeling -> Koppelcode maken) en worden als kant-en-klare add-on-opties
aangeleverd — gewoon overtypen of plakken.

## Koppelen

1. Vraag de hubbeheerder om een koppelcode voor jouw instantie.
2. Installeer deze add-on (via de HA Fleet add-on-repository, of door de map
   handmatig naar `/addons/hafleet_agent` te kopiëren).
3. Vul `hub_url`, `agent_id` en `agent_key` in bij **Configuratie** en sla op.
4. Start de add-on. In het logvenster verschijnt `verbonden met de hub` zodra
   de tunnel staat — de hubbeheerder ziet de instantie dan als "verbonden".
5. Sleutel kwijt, gelekt, of wil je opnieuw koppelen? De hubbeheerder kan de
   sleutel roteren; vul de nieuwe `agent_key` hier in en herstart de add-on.

## End-to-end versleutelde backups

Backups die de hub op deze instantie aanvraagt (handmatig of via de wekelijkse
planning) worden **altijd versleuteld** met een wachtwoord dat alleen op deze
machine bestaat: `/data/backup_key`, automatisch aangemaakt bij de eerste
start. De hub — en dus de hubbeheerder — ziet dat wachtwoord nooit en kan de
backup niet openen; jij als eigenaar wel.

**Belangrijk:** bij de eerste start plaatst de add-on een notificatie in je
HA-UI met het wachtwoord. Bewaar het op een veilige plek búiten deze machine
(bijv. een wachtwoordmanager). Als de machine volledig wegvalt, heb je dit
wachtwoord nodig om de backup te herstellen — zonder is de backup onbruikbaar.
Stuurt de hub zelf een wachtwoord mee, dan wordt dat genegeerd: het eigen
wachtwoord wint altijd.

## Logs

Bij verbindingsproblemen: bekijk het add-on-logvenster. De meest voorkomende
melding is dat `hub_url` / `agent_id` / `agent_key` nog leeg staan — de add-on
wacht dan stil en probeert het niet totdat de configuratie is aangevuld.
