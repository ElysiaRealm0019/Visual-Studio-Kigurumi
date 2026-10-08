# Visual Studio Kigurumi (V.S.K)

[中文](README.zh-CN.md) | English

<p align="center">
  <img src="docs/logo.png" alt="Visual Studio Kigurumi logo" width="160" />
</p>

Visual Studio Kigurumi (V.S.K) is a web tool for making Kigurumi head-shell preview images, developed based on [KigCraft](https://kigcraft.com). Each character is a project: chat with the agent on the right to analyse references, generate and revise the front view, and make the four-view sheet, or adjust the image directly with the manual tools (proportion, face, eyes, brows, mouth, liquify, annotation, local generation). Every result lands in the project's version history.

## About this project

V.S.K is an independent project developed based on [KigCraft](https://kigcraft.com), the original Kigurumi head preview tool by SeaRabbit / 海兔. The conversational workflow, workspace concept, and generation pipeline come from the KigCraft codebase; this project extends them with pluggable generation backends (Codex, Claude Code, SiliconFlow, Volcengine Ark), editor deformation refinements, and generation prompt tuning.

Upstream credits: KigCraft by SeaRabbit / 海兔 — <https://kigcraft.com> — user group QQ 934715528.

## Features

- Project workspace: a VS Code-like layout with an explorer (references, version tree) and tool panels on the left, a tabbed canvas in the middle, the agent chat on the right, and a status bar; dark and light themes.
- Manual editing: proportion, face, eye, brow, and mouth sliders deform the image live (mesh warping in the browser, no AI call), plus liquify, annotation, and local generation. Saving creates a new version (Ctrl+S).
- Conversational flow: an LLM acts as the middleware and calls reference analysis, front-view generation, front-view revision, and four-view generation as the conversation needs; it also sees versions you saved by hand.
- Checkpoint before spending more: the four-view sheet is only generated after the user approves a front view (the "Use this one" button or an explicit yes in the chat).
- A per-message generation cap (`AGENT_MAX_GENERATIONS_PER_TURN`) stops the model from generating in a loop.
- Conversations are saved locally, so you can return to an earlier design and keep revising.
- The LLM, image, and orchestration backends are configured separately (Codex, Claude Code, SiliconFlow, Volcengine Ark, ...).
- Chinese, English, and Japanese UI.

## License

Visual Studio Kigurumi (V.S.K) is released under GPL-3.0-or-later, following the upstream KigCraft license. See [LICENSE](LICENSE).

## Requirements

- Docker Desktop or Docker Engine with Compose
- Node.js 22 or newer for frontend-only development
- Python 3.12 or newer for backend-only development
- Codex CLI authentication when `GENERATION_PROVIDER=codex`

## Quick start

```powershell
Copy-Item .env.example .env
# Edit .env and replace every change-me-* value before exposing the service.
docker compose up --build
```

Before using Codex generation, create `ref/` at the repo root and add your own product reference images. See [Product reference images](#product-reference-images).

### Run locally without Docker

Local development does not need Docker: generation jobs run inside the API process, job state lives in SQLite, and outputs go to `runtime/`, so Postgres, Redis, and MinIO are not used.

```powershell
Copy-Item .env.example .env   # set LLM_PROVIDER / IMAGE_PROVIDER etc.
.\scripts\start-local.ps1
```

The script creates `backend\.venv` and runs `npm install` when dependencies are missing, then starts the backend (`127.0.0.1:18000`) and the frontend (<http://localhost:5173>). Ctrl+C stops both. Pass `-Lan` to open the frontend from a phone on the same network. If `codex` is not on PATH, the script uses `codex.exe` from the official install location.

Local URLs with Docker:

- Frontend: <http://localhost:15173>
- API health: <http://localhost:18000/health>
- MinIO console: <http://localhost:19001>

## Generation providers

`GENERATION_PROVIDER=codex` runs generation through the Codex CLI inside the backend container. Mount an authenticated Codex config directory at runtime:

```powershell
Copy-Item -Recurse "$env:USERPROFILE\.codex" ".\runtime\codex-home"
docker compose up --build
```

For a Linux server, copy an authenticated Codex config directory to the host and set:

```dotenv
GENERATION_PROVIDER=codex
CODEX_PATH=codex
CODEX_CONFIG_DIR=/home/deploy/.codex
CODEX_PRODUCT_REFERENCE_PATH=ref/product-reference.png
```

### Separate LLM and image backends

Generation uses two kinds of models:

- **LLM** (`LLM_PROVIDER`): reference safety check and detail analysis. Options: `codex`, `claude_code`.
- **Image** (`IMAGE_PROVIDER`): front view, four-view sheet, and local revision. Options: `codex`, `codex_bridge`, `siliconflow`, `ark`.

When both are empty they are derived from `GENERATION_PROVIDER`, so existing setups keep working. A setup with no Codex dependency:

```dotenv
LLM_PROVIDER=claude_code
IMAGE_PROVIDER=siliconflow
SILICONFLOW_API_KEY=your-key
```

**Claude Code** needs a logged-in Claude Code config directory, mounted at `/root/.claude`:

```powershell
Copy-Item -Recurse "$env:USERPROFILE\.claude" ".\runtime\claude-home"
```

Claude models cannot generate images, so `LLM_PROVIDER=claude_code` always needs a separate image backend. On macOS the login is stored in the Keychain, so copying the directory does not carry it; log in on a Linux or Windows host and copy from there.

**SiliconFlow** uses `Qwen/Qwen-Image-Edit-2509` by default and sends at most three images per request:

- front view: character front reference + `ref/product-reference.png` + one contact sheet of the other references;
- four-view sheet: the approved front view + `ref/turnaround-reference.png`;
- local revision: only the masked region is cropped and edited, then composited back with the mask.

Four-view sheets keep the model's native resolution and are not upscaled. The platform watermark is turned off in the request, and V.S.K's own AI-generated watermark is always applied.

**Volcengine Ark (Agent Plan)**: `IMAGE_PROVIDER=ark` uses `doubao-seedream-5-0-pro` by default and sends all reference images in one `image` list, capped by `ARK_MAX_REFERENCE_IMAGES` (default 4; extra references are merged into one contact sheet). It needs the Agent Plan dedicated API key (`ARK_API_KEY`); other Ark keys do not work with Agent Plan. Usage is deducted in AFP: the first input image is free, each further one costs 10 AFP, and each output image costs 150 AFP (300 above about 2.61 MP). Four-view sheets request `1920x1280` by default (3:2, billed at 150 AFP); with `2K` the model picks its own aspect ratio and the backend pads the result to 3:2 with the background colour.

Try the API first with the probe script:

```powershell
$env:SILICONFLOW_API_KEY = "your-key"
backend\.venv\Scripts\python tools\siliconflow_probe.py size-test
```

### Chat assistant (orchestrator LLM)

The chat UI is driven by an orchestrator LLM that interprets the conversation and calls tools. It is separate from `LLM_PROVIDER` above (reference safety check and detail analysis):

- `AGENT_LLM_PROVIDER=openai_compatible` (default): any OpenAI-compatible endpoint with tool calling. Defaults to Volcengine Ark Agent Plan `doubao-seed-2-0-pro` with reasoning turned off (about 5 s per step). An empty `AGENT_LLM_API_KEY` reuses `ARK_API_KEY`.
- `AGENT_LLM_PROVIDER=claude_code`: orchestrate with the logged-in Claude Code. No extra key, but each step starts a CLI process, so it is slower.

Recommended setup:

```dotenv
LLM_PROVIDER=claude_code
IMAGE_PROVIDER=ark
ARK_API_KEY=your-agent-plan-key
```

### Product reference images

This repository does not ship product reference images. You need to create `ref/` yourself and place your own files there before running Codex generation.

| File | Purpose |
| --- | --- |
| `ref/product-reference.png` | Finished-product style reference for front-view generation |
| `ref/turnaround-reference.png` | Finished-product style reference for four-view generation |

Use PNG files with the exact filenames above. They constrain the finished head-shell style of generated results: studio background, shell material, wig texture, framing, and lighting. They are not the character reference images uploaded through the UI.

`ref/` is listed in `.gitignore`, so private reference assets stay on your machine and are not committed to Git.

```powershell
New-Item -ItemType Directory -Force ref
# Copy your own reference images into ref/
```

Override the front-view reference path in `.env` if needed:

```dotenv
CODEX_PRODUCT_REFERENCE_PATH=ref/product-reference.png
```

`GENERATION_PROVIDER=codex_bridge` runs the Codex CLI outside the backend container. Start the bridge with:

```powershell
.\tools\start_codex_bridge.ps1
```

Use a custom `CODEX_BRIDGE_TOKEN` outside local development.

Fixture and mock generation are for tests and local smoke runs only. Do not enable them in production.

## Deployment

Create a production `.env` on the server before deploying. At minimum, set:

- `APP_ENV=production`
- Strong values for `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `JWT_SECRET`, and `ADMIN_AUDIT_PASSWORD`
- Production `CORS_ALLOWED_ORIGINS`
- `GENERATION_PROVIDER=codex` or `codex_bridge`
- `ALLOW_FIXTURE_GENERATION=false`

Deploy the current Git commit over SSH:

```powershell
.\scripts\deploy-ssh.ps1 `
  -KeyPath "$env:USERPROFILE\.ssh\id_ed25519" `
  -SshTarget "deploy@example.com" `
  -RemoteAppDir "/opt/vsk"
```

The script uploads a `git archive`, extracts it on the server, checks that production is not using fixture generation, and rebuilds `api`, `worker`, and `frontend` with Docker Compose.

## Development

Frontend:

```powershell
cd frontend
npm install
npm run dev
npm run build
npm test
```

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m pytest
```

Design notes live in [docs/plans](docs/plans/).
