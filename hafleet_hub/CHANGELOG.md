# Changelog

## 1.2.1

- Backups-tab toont een slotje bij versleutelde backups. Agent-instanties met
  agent 1.3.0+ versleutelen hun backups end-to-end — het wachtwoord bestaat
  alleen op de machine zelf, de hub kan ze niet openen.

## 1.2.0

- Add-on-opties toegevoegd: `public_hub_url` (voor de koppelcodes) en
  `poll_interval` — de hub is nu volledig deelbaar zonder code-aanpassing.
- Watchdog: de supervisor herstart de hub automatisch als `/healthz` niet
  meer antwoordt.
- Poller draait nu parallel per instantie — één trage of onbereikbare
  instantie houdt de statusronde (en de alerts van de rest) niet meer op.
- Audit-log heeft een eigen lock: audit-schrijfacties blokkeren de rest van
  de hub niet meer.
- Store-packaging: icoon/logo, vertalingen (NL/EN) en documentatie.

## 1.1.0

- Alerts-engine (offline / backup te oud / core-update / dode entities /
  schijf vol / unhealthy) met pushnotificaties en 24u-cooldown.
- Audit-log, Instellingen-modal, Integraties-tab met entry-reload,
  sleutel-rotatie per agent, host-metrics, wekelijkse backup-planner.
- Hardening: agent-allowlist, rate-limiting op agent-auth, frame-cap 8 MB,
  ingress-IP-restrictie, padnormalisatie tegen traversal.
- Premium dark-glass web-UI met gezondheidsring per instantie, a11y-pass
  (focus-ringen, aria, touch-targets) en mobiele layout.

## 1.0.0

- Eerste versie: hub-microservice + web-UI (ingress), directe instanties
  (URL + token) en agent-instanties via een uitgaande WebSocket-tunnel.
