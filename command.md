# Start the portfolio

The frontend is the static site in `public/`. The Python backend serves those
files and the API from the same local server, so you do not need a separate
frontend development server.

Open PowerShell in the project root and run:

```powershell
.\.venv\Scripts\python.exe -m backend.server
```

Then open [http://127.0.0.1:3000](http://127.0.0.1:3000).

## First-time setup

If the virtual environment or dependencies are missing:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If `backend\.env` does not exist, create it from the example once:

```powershell
Copy-Item backend\.env.example backend\.env
```

Edit `backend\.env` to add your private OpenRouter key and local admin settings.
Do not overwrite an existing `.env`; it may already contain your private
credentials. Local dashboard submissions are stored in the ignored SQLite file
`backend\visitor_leads.sqlite3`.

## Stop the server

Press `Ctrl+C` in the PowerShell window running the server.
