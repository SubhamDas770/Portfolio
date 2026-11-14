# Portfolio backend

The Python API powers the digital twin, optional GitHub repository/README
display, and the consent-based visitor contact form. The static site lives in
`../public`; profile source material stays under `data/` and is not served as
public content.

## Local development

From the project root on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item backend\.env.example backend\.env
.\.venv\Scripts\python.exe -m backend.server
```

If `backend\.env` already exists, update it manually instead of overwriting it.
Set `OPENROUTER_API_KEY` to a current key. Never put it in `public/` or browser
code. The default model is `openai/gpt-4.1-mini`. Set `GITHUB_USERNAME` to
`SubhamDas770` to load the public repositories section.

For local development, the contact form, conversation archive, and dashboard
use a SQLite database at `backend/visitor_leads.sqlite3` when no Postgres URL
is configured. This local file is private and is not included in Vercel
deployments. The chat itself requires a configured OpenRouter key. Set a strong
`ADMIN_PASSWORD` and an
`ADMIN_SESSION_SECRET` (at least 32 characters). To test scheduled cleanup
locally, also set `CRON_SECRET`. Generate random secrets with:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

For local development, changing `ADMIN_PASSWORD` in `backend\.env` takes
effect on the next dashboard sign-in; a backend restart is not required.

The database table and index are created on first use. Open
`http://127.0.0.1:3000/admin` for the owner dashboard.

### Local Docker

Docker is for local development and validation only; Vercel continues to run
the hosted application as serverless Functions. Start Docker Desktop, make sure
`backend\.env` exists, then build and run the image from the project root:

```powershell
docker build -t subham-portfolio:local .
docker volume create subham-portfolio-data
docker run --rm --name subham-portfolio `
  --env-file backend\.env `
  -p 3000:3000 `
  -v subham-portfolio-data:/data `
  subham-portfolio:local
```

The image runs as a non-root user. The named Docker volume stores the local
SQLite database at `/data/visitor_leads.sqlite3`, and credentials are passed at
runtime rather than copied into the image. To use the local SQLite database,
leave `POSTGRES_URL` and `DATABASE_URL` unset in the environment file.

## Chat sessions, archives, and visitor privacy

When a conversation starts with a simple greeting, the digital twin introduces
itself and asks for the visitor's name, then asks their company in a separate
reply. Both questions are optional; visitors can type `skip` or ask their
question instead. The volunteered name/company remain out of the model's chat
history, but the introduction and all other conversation messages are saved to
the server-side archive. Visitors should not share sensitive information in
chat.

Each page load starts a new random session ID. The active transcript is kept in
page memory rather than restored from browser storage. A server restart also
starts a fresh active conversation; the page detects this automatically or
when the next request reports a stale server session. Conversation archives
are stored indefinitely in Postgres in production and in the local SQLite
database during development. They are visible in the password-protected owner
dashboard at `/admin` and can be deleted there. There is no automatic archive
retention or expiry. Configure Postgres before deploying to Vercel; SQLite is
only the local-development fallback.

On Vercel, the session's server marker uses the deployment URL so separate
serverless instances for one deployment accept the same active session. A new
deployment changes that marker and causes open pages to start fresh. Locally,
the marker is unique to each backend process.

This archive is separate from the contact form, which stores details only
after the visitor explicitly submits its consent form.

## Vercel deployment

The Python entrypoint is configured as `backend.server:app`; runtime
dependencies are declared in the root `pyproject.toml` and mirrored in
`requirements.txt` for local and Docker installs. Vercel serves the static
`public/` files and runs API requests as serverless Functions. Production
requires Postgres; SQLite is only for local development.

Terraform configuration for the Vercel project and Neon Postgres is in
[`infra/terraform/`](../infra/terraform/README.md). It targets Vercel Hobby and
Neon Free, uses Singapore regions by default, and constrains Neon compute and
storage to the published free allocations. Review that guide for PowerShell
commands to initialize Terraform, inspect the plan, configure secrets, and
deploy. Do not apply a plan until you have checked the resources and current
provider pricing.

Configure these production-only variables through the Vercel dashboard or CLI;
do not commit them or place their values in Terraform configuration:

- `OPENROUTER_API_KEY`: a newly rotated key; model inference may incur separate
  usage charges.
- `OPENROUTER_MODEL`: optional; defaults to `openai/gpt-4.1-mini`.
- `POSTGRES_URL`: Neon connection string, required for hosted archives and
  contact submissions.
- `ADMIN_PASSWORD`: a unique, strong dashboard password.
- `ADMIN_SESSION_SECRET`: a random secret of at least 32 characters.
- `CRON_SECRET`: a separate random secret for scheduled tasks.
- `PUBLIC_SITE_URL`: deployed HTTPS origin used in OpenRouter request metadata.
- `GITHUB_USERNAME`: set to `SubhamDas770` for public repositories.
- `GITHUB_TOKEN`: optional, read-only public repository access; keep server-side.

Terraform state is sensitive: the Neon provider returns a database password and
connection URI in state even though those attributes are marked sensitive.
Keep local state private and out of version control; the configuration does not
manage Vercel environment-variable values. Do not add production database
credentials to preview environments unless their writes to production are
intended.

`vercel.json` schedules cleanup daily and refreshes the GitHub project cache
every two days. On Vercel, projects are fetched when `/api/projects` is
requested rather than delaying server startup; the scheduled refresh warms the
cache every two days. Local development refreshes the list when the backend
starts and in the background every two days. Requests after the cache expires
also trigger a refresh. The Vercel scheduled refresh uses the `CRON_SECRET`
bearer token.

Vercel Hobby, Neon Free, and other providers have usage limits and may pause or
reject requests after quotas are reached. Free-tier availability and pricing
can change. Check each account's current terms and set provider-side usage or
spending limits; a free hosting tier does not make OpenRouter inference free.

Submissions older than 90 days are deleted; the dashboard also excludes records
older than that period and allows the owner to delete a submission sooner.
Conversation archives do not share the leads' 90-day retention policy and
remain until manually deleted. Leads are stored only after the visitor submits
the form and explicitly checks the consent box. The site does not try to
identify anonymous visitors or store their IP addresses. Enable Vercel Firewall
rate limits for `/api/chat`, `/api/conversations/*`, and `/api/leads` before
public launch; the local chat limiter is only per process and is not a
distributed deployment limit.

GitHub projects are fetched from the configured public profile at
`/api/projects`. All public repositories returned by the profile are listed,
including forks and repositories without a root README. If the GitHub REST API
rate limit is reached, the backend falls back to public profile/repository
pages and raw README files. The page safely renders repository names and README
text as plain text; an explicit note is shown when a repository has no root
README. No repository list is invented when `GITHUB_USERNAME` is unset.
