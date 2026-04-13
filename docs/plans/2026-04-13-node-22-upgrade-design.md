# Node.js 18 → 22 LTS Upgrade — Design

Issue: #71

## Goal

Upgrade the VM's Node.js from 18.19.1 (EOL April 2025) to 22 LTS (Jod, supported through April 2027) and verify the JS test suite, lint, and format tooling still pass.

## Background

- Node is dev-only on this VM: used for `vitest` (28 tests), `eslint`, `prettier`. Prod runtime is FastAPI/Python — Node is not in the request path.
- Current install: `sudo apt install nodejs` → Ubuntu 24.04 noble ships 18.x in universe.
- `happy-dom` was chosen over `jsdom` v29 due to a CJS/ESM incompatibility under Node 18. Node 22 would unblock jsdom, but we are not switching — happy-dom is faster and works.

## Approach

### Install method: NodeSource (system-wide)

- Add NodeSource 22.x apt repo, purge the old `nodejs` package, install new one.
- Rationale: single-tenant VM, no version-juggling needs, no shell-init surprises. nvm's per-user model adds complexity with no payoff here.
- Rollback: `apt purge nodejs && rm /etc/apt/sources.list.d/nodesource.list`, then reinstall from universe.

### Steps

1. Install Node 22 via NodeSource:
   ```bash
   curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
   sudo apt install -y nodejs
   ```
2. Verify: `node --version` → `v22.x.x`, `npm --version` → `10.x`.
3. Bump `package.json` `engines.node` → `">=22"`.
4. Clean regen of lockfile: `rm -rf node_modules package-lock.json && npm install`.
5. Verify suite:
   - `npm run test:js` → 28 tests pass
   - `npm run lint:js` → clean
   - `npm run format:js:check` → clean

### Out of scope

- Switching happy-dom → jsdom. Not needed; happy-dom works and is faster.
- CI pipeline changes — none exists yet.
- Upgrading vitest, eslint, prettier, happy-dom majors.

## Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Install method | NodeSource (apt) | System-wide, simpler than nvm for single-tenant VM |
| Target version | 22 LTS (Jod) | Current active LTS through 2027-04; 24 not yet LTS |
| `engines` constraint | `">=22"` | Matches existing `">=18"` style |
| happy-dom vs jsdom | keep happy-dom | No functional reason to switch |
| Lockfile | regenerate from scratch | Avoid Node-18-resolved artifacts in lockfile |

## Verification

- `node -v` reports v22
- `npm run test:js` 28/28 pass
- `npm run lint:js` exit 0
- `npm run format:js:check` exit 0
- `package.json` shows `"node": ">=22"`
- `package-lock.json` regenerated (check `lockfileVersion` and diff)

## Rollback

If Node 22 breaks tests and can't be fixed quickly:

```bash
sudo apt purge nodejs
sudo rm /etc/apt/sources.list.d/nodesource.list
sudo rm -f /etc/apt/keyrings/nodesource.gpg       # installed by setup_22.x
sudo apt update && sudo apt install -y nodejs    # back to 18.x from universe
git checkout package.json package-lock.json
npm install
```
