# Remote Print Network — Render/GitHub Deployable Demo

This is a hosted Flask version of the remote print network prototype. It combines the **registration portal** and **routing hub** into one deployable web app.

The older module-number labels are intentionally not used in the UI or code comments. The two hosted responsibilities are:

- **Registration portal**: users, roles, device registration, persistent registration/session codes, account/device approval, and audit records.
- **Routing hub**: node connections, device/author presence, job bundles, per-instance job codes, one-time start authorization, completion acknowledgement, and simulated settlement.

Desktop clients such as the Author Node and Device Node should remain local applications. They connect outbound to this hosted app over HTTPS.

## Included files

```text
app.py
requirements.txt
Procfile
render.yaml
runtime.txt
templates/
static/
local_client_notes/
```

## Local run

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5055
```

Default local admin, unless overridden with environment variables:

```text
Email: admin@example.com
Password: admin123
```

For any public demo, override these before deployment.

## Environment variables

```text
FLASK_SECRET_KEY              Required for deployed sessions
DATABASE_URL                  PostgreSQL URL on Render; SQLite fallback locally
ADMIN_EMAIL                   Initial admin email
ADMIN_INITIAL_PASSWORD        Initial admin password
PORT                          Provided by Render automatically
```

## Render deployment path

1. Create a new GitHub repository.
2. Copy this project folder into the repository root.
3. Commit and push.
4. In Render, create a Blueprint or Web Service from the repository.
5. The included `render.yaml` creates:
   - a Python web service
   - a PostgreSQL database
   - generated `FLASK_SECRET_KEY`
   - `DATABASE_URL` wired from the database
6. Change `ADMIN_EMAIL` and `ADMIN_INITIAL_PASSWORD` in Render before your demo.
7. Deploy.

The app is started with:

```bash
gunicorn app:app
```

## Current API endpoints

### Registration / activation

```text
POST /api/registration/activate
POST /api/routing/connect
POST /api/routing/heartbeat
POST /api/routing/disconnect
GET  /api/routing/status/<connection_id>
```

Registration portal codes are persistent until deactivated. They are not one-time job codes.

### Job routing

```text
POST /api/jobs/register-bundle
GET  /api/jobs/available
POST /api/jobs/claim
POST /api/jobs/start
POST /api/jobs/complete
POST /api/jobs/acknowledge
GET  /api/jobs/author-completions
```

Job bundles are limited to 1–5 print instances. Every print instance has its own one-time `author_job_code`. A job instance must be started through the routing hub before it can become payable. Aborted and failed jobs pay `$0.00`. Duplicate completion events are rejected.

## Example API flow

1. User registers in the registration portal.
2. Admin/moderator approves the user and any device.
3. User generates a persistent registration portal code.
4. Local Author Node connects with that code as `author_node`.
5. Local Device Node connects with its own code as `device_node`.
6. Author Node registers a bundle of up to 5 job instances.
7. Device Node claims one instance.
8. Device Node requests one-time start authorization.
9. Device Node completes or aborts.
10. Author Node acknowledges completion.
11. Routing hub transfers simulated funds once for that instance.

## Important prototype limits

Not included yet:

- real G-code streaming
- encryption/decryption
- Merkle hashing
- blockchain anchoring
- real payment transfer
- real USB printer control
- production-grade CSRF/rate-limiting hardening

This version is meant to make the hosted data flow demonstrable before adding those controls.

## Updating local clients

Set the local Author Node and Device Node routing URL to your Render URL, for example:

```text
https://your-render-app.onrender.com
```

The local clients should use the API endpoints above. See `local_client_notes/client_api_mapping.md` for payload examples.
