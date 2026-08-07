# InariSoftLabs Marketing Agent

A self-hosted Python dashboard that turns verified business knowledge and your product visuals into reviewable Facebook drafts, then publishes them on a configurable schedule.

## Why no vector database yet?

The first version uses SQLite with FTS5 full-text search. Your source material is likely a compact set of product descriptions, target audiences, testimonials, and case studies. FTS5 makes retrieval fast, local, auditable, and easy to correct—without the operational burden or opaque matches of a vector database.

Add a vector database only when you have a large, messy corpus (hundreds or thousands of pages), many semantically similar documents, or want cross-document question answering. At that point, use **Postgres + pgvector** if this app already has Postgres, or **Qdrant** for a dedicated retrieval service. Keep the existing SQLite/SQL facts as the source of truth; semantic retrieval should augment—not replace—the fact checks.

## Run it

```bash
cp .env.example .env
python3 -m pip install -r requirements.txt
python3 run.py
```

Open `http://localhost:8000`. Set `DASHBOARD_PASSWORD` before exposing it to the internet; sign in with username `admin`.

## First-use checklist

1. Add verified information in **Knowledge**—products, ideal customers, supported outcomes, case studies, and calls to action.
2. Upload product screenshots or videos. Select these assets for the final Facebook post. DeepSeek's current API models are text-only, so describe the visible workflow in the **Visual context** box; this stops the writer guessing visual details. For videos, a representative screenshot is still useful for review and creative direction.
3. Set `OPENAI_API_KEY` to enable OpenAI for draft creation. The default is `gpt-5.6-luna` with low reasoning effort, tuned for cost-sensitive post generation. OpenAI is preferred when configured, followed by Gemini and then DeepSeek as transition fallbacks. Screenshots are not sent to any writing model—only their reviewed labels and descriptions are used.
4. Add Facebook Page settings to `.env`: Page ID, Page access token, and the Meta Graph API version enabled in your Meta app.
5. Start in **Review every draft** mode. Once you trust the outputs, choose a 1×, 2×, or 3× daily cadence and explicitly enable automatic publishing.

### Make the writing sound like you

The cadence form accepts one or two optional **Human writing examples**. Paste posts
whose rhythm, restraint, and vocabulary feel right for InariSoftLabs. Examples guide
voice only: product claims still have to come from approved knowledge records. When
the field is blank, the agent uses a built-in owner-style example in the requested
language and avoids common AI marketing clichés.

Generated drafts remain editable before publication. Each draft shows the knowledge
records it cited, and publishing requires confirmation. Website imports are marked
unreviewed and cannot reach the writing model until you approve them in the dashboard.

`python scripts/import_site.py https://inarisoftlabs.com` imports public site text as one reviewable knowledge entry. Use it as a starting point, not as permission to publish unreviewed claims.

### Product knowledge packs

Version-controlled knowledge packs let you ground marketing copy in a product's
actual specifications. Import the LabLink pack with:

```bash
python3 scripts/import_knowledge_pack.py knowledge/lablink.json
```

The importer is safe to repeat: it updates only records with the pack's stable
IDs and keeps dashboard entries you added manually. The LabLink pack includes
explicit claim-hold rules for undocumented voice calls and conflicting product
specifications, so those are not accidentally turned into marketing claims.

## Safeguards

- Drafts only use knowledge currently stored in the library and ask the model not to invent claims.
- A similarity check prevents near-duplicate recent posts.
- Auto-publishing is disabled by default.
- Failed Facebook sends remain in the dashboard with their error message.
- Images publish as Facebook photo posts; a selected video publishes as a video post. Facebook credentials never reach the browser.
- DeepSeek writes the post through its OpenAI-compatible Chat Completions API with JSON output. It does not receive the uploaded image files.

## Production notes

The built-in scheduler runs while the web process is alive. For reliable production delivery, run the app as a persistent service (for example, Docker/Cloud Run with an always-on worker, a VM, or a separate scheduled call to `/api/scheduler/run`). Back up `data/marketing-agent.db` and `data/uploads/` regularly.

Set `APP_ENV=production` in production. The application then refuses to start without
`DASHBOARD_PASSWORD`. The health check is available at `/healthz`. Set
`MARKETING_AGENT_DATA_DIR` if the SQLite database and uploads should live outside the
repository directory.

Only one process should run the built-in scheduler. Set
`ENABLE_INTERNAL_SCHEDULER=false` on additional web workers and trigger
`POST /api/scheduler/run` from one authenticated worker or an external scheduler.

## Development checks

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
ruff check app scripts tests run.py
ruff format --check app scripts tests run.py
pytest -q
node --check public/app.js
```

GitHub Actions runs the Python lint, formatting, and test checks on pushes and pull
requests.

## Deploy

The production deployment uses Docker Compose and preserves `data/` on the server.

```bash
./deploy.sh
```

It deploys to `/home/shampad/projects/isl-marketing-agent` on `192.168.0.131`
and publishes the dashboard on port `8020`. Use `./deploy.sh --help` to override
the host, remote path, or port.
