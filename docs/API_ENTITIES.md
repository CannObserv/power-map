# power-map Public API — Resources

Per-resource endpoint behaviour: filters, response shapes, and the implicit rules
each collection follows. Auth, pagination, conditional requests and the change feed
are in `docs/PUBLIC_API.md`; write semantics in `docs/OBSERVATIONS.md`, except
assignments', which live with the endpoint in `docs/API_ASSIGNMENTS.md`.

---

Each resource has its own document — load the one you are working on, not the set:

| Resource | Document | Covers |
|---|---|---|
| People | [API_PEOPLE.md](API_PEOPLE.md) | list/detail filters, `POST /people/observations` |
| Organizations | [API_ORGS.md](API_ORGS.md) | detail shape, renames and the name timeline, `POST /orgs/observations` |
| Roles | [API_ROLES.md](API_ROLES.md) | list/detail, `GET /role-types`, `POST /roles/observations` |
| Assignments | [API_ASSIGNMENTS.md](API_ASSIGNMENTS.md) | list/detail, `POST /assignments/observations` |
| Jurisdictions | [API_JURISDICTIONS.md](API_JURISDICTIONS.md) | endpoints, `POST /jurisdictions/observations`, implicit behaviors |
| Entity Events | [API_EVENTS.md](API_EVENTS.md) | `GET /{entity}/{id}/events` response shape, the observation `events` surface |

Cross-resource: `GET /api/v1/entity-identifier-types` is the identity vocabulary **every** observation addresses by (#459) — query it instead of hardcoding slugs; per-slug value conventions live in [OBSERVATIONS.md](OBSERVATIONS.md) §"Identifier types".
