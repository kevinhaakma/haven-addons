# Changelog

## 2.0.0 — Haven

- **Nieuwe naam: Haven Agent** — voorheen "HA Fleet Agent". Nieuwe slug
  (`haven_agent`), dus dit is een nieuwe installatie; de add-on-opties
  (hub_url / agent_id / agent_key) zijn dezelfde drie als voorheen.
- Log en notificaties in de nieuwe huisstijl ("aangemeerd" zodra de tunnel
  naar de haven staat).

## 1.3.0

- **End-to-end versleutelde backups**: backups die via de hub worden
  aangevraagd, versleutelt de agent met een wachtwoord dat alleen op deze
  machine bestaat (`/data/backup_key`). De hub ziet het wachtwoord nooit —
  alleen de eigenaar kan zijn eigen backups openen of herstellen. Bij de
  eerste start verschijnt een notificatie in de HA-UI met het wachtwoord;
  bewaar dat buiten deze machine (zonder wachtwoord is een backup niet te
  herstellen als de machine wegvalt).

## 1.2.0

- Core-allowlist vernauwd tot precies de endpoints die de hub gebruikt,
  inclusief method-check (GET/POST) — een gecompromitteerde hub kan geen
  willekeurige service-calls of config-wijzigingen meer doen op deze
  instantie.
- Store-packaging: icoon/logo, vertalingen (NL/EN) en documentatie.

## 1.1.0

- Begrensde worker-pool (geen thread-per-bericht), frame-cap 8 MB,
  padnormalisatie in de allowlist, add-on-versie in de hello-metadata.

## 1.0.0

- Eerste versie: uitgaande WebSocket-tunnel naar de hub, lokale uitvoering
  met SUPERVISOR_TOKEN, vaste supervisor-allowlist.
