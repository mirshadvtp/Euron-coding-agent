# Publishing guide

Everything here is **one-time setup**. After it's done, releasing is just:

```bash
# bump version in extension/package.json and backend/pyproject.toml, then:
git tag v0.1.0
git push origin v0.1.0      # GitHub Actions builds + publishes everything
```

---

## 0. Names already wired up

These are already set throughout the repo:

- **PyPI distribution:** `euron-coding-agent` (`pyproject.toml` `name` and
  `extension/src/extension.ts` `BACKEND_PACKAGE` — they must always match).
- **Import package / CLI:** `euron_agent` / `euron-agent` (internal, unchanged).
- **GitHub repo:** `https://github.com/euron-tech/Euron-coding-agent`.
- **VS Code publisher:** `euron-tech` — you must create this publisher id on the
  Marketplace (step 2) before `vsce publish` will work.

## 1. PyPI (the backend package)

1. Create an account at https://pypi.org and verify email.
2. Account settings → **API tokens** → create a token (scope: entire account for
   the first upload, then narrow to the project).
3. Add it to the GitHub repo: **Settings → Secrets and variables → Actions →**
   `PYPI_API_TOKEN`.

Test the first upload manually if you like:
```bash
cd backend && python -m build && python -m twine upload dist/*
```

## 2. VS Code Marketplace (the extension)

1. Create an **Azure DevOps** organization: https://dev.azure.com.
2. Create a **Personal Access Token**: User settings → Personal access tokens →
   New. Scope: **Marketplace → Manage**. Organization: **All accessible**.
3. Create the publisher at https://marketplace.visualstudio.com/manage and use
   its id as `publisher` in `extension/package.json`.
4. Add the PAT to GitHub secrets as `VSCE_PAT`.

## 3. Open VSX (Cursor, VSCodium, Windsurf, Gitpod)

Microsoft's Marketplace doesn't serve the VS Code forks — Open VSX does.

1. Sign in at https://open-vsx.org with GitHub and sign the publisher agreement.
2. Create an **Access Token** (Settings → Access Tokens).
3. Create a namespace matching your `publisher`:
   `npx ovsx create-namespace <publisher> -p <token>`.
4. Add the token to GitHub secrets as `OVSX_PAT`.

## 4. Release

```bash
git tag v0.1.0 && git push origin v0.1.0
```

The **Release** workflow publishes the backend to PyPI first, then the extension
to both Marketplace and Open VSX, and attaches the `.vsix` to a GitHub release.

---

## How an end user gets it (what you're enabling)

1. They install **Euron Coding Agent** from the Marketplace / Open VSX.
2. On first run the extension auto-creates a private Python venv and
   `pip install`s `euron-agent` from PyPI (needs Python 3.9+ on their machine).
3. They pick a provider and paste a key (stored in SecretStorage).
4. Done — no terminal, no `.env`, no manual server.

## Going fully zero-dependency (no Python required) — later

To reach users without Python, ship a PyInstaller binary instead of a PyPI
install:

- Build per-OS with a CI matrix (`windows-latest`, `macos-latest`,
  `ubuntu-latest`): `pyinstaller --onefile -n euron-agent-server backend/euron_agent/__main__.py`.
- Attach the binaries to the GitHub release; have the extension download the
  matching one on first run instead of provisioning a venv.
- For a smooth experience you must **code-sign**: Apple notarization (Apple
  Developer Program, ~$99/yr) and a Windows signing certificate, or users hit
  Gatekeeper / SmartScreen warnings. This is the main reason it's a later step.
