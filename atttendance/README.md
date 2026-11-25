# SmartAttend AI

SmartAttend AI is a school attendance application with a FastAPI/PostgreSQL backend, React web client, and Expo mobile client. Face matching uses InsightFace embeddings; it never substitutes filename matching or a simulated model. Biometric enrollment requires guardian consent, and embeddings are encrypted before storage.

> **Privacy and deployment:** This repository is a development foundation, not a compliance certification. Do not enroll real students until the school has completed its legal/privacy review, obtained documented guardian consent, secured the deployment, and configured retention and access policies. Use the manual attendance workflow when consent, camera access, or the AI model is unavailable. Spoof-resistant liveness is not included; automatic marking is disabled by default.

## Project layout

- `backend/` — FastAPI, SQLAlchemy, JWT, face engine, Alembic, tests
- `web/` — Vite + React role-based dashboard and teacher camera workflow
- `mobile/` — Expo/React Native login, role dashboards, history and teacher camera workflow
- `docs/` — architecture, data model, API and privacy notes

## Requirements

- Python 3.11+ (3.12 recommended; InsightFace/ONNX Runtime wheel support varies by platform)
- Node.js 20+
- PostgreSQL 15+ (or SQLite for local API/test use)
- For face recognition: InsightFace, ONNX Runtime, and the configured pretrained model files. The first model load can require a network connection unless models are provisioned locally.

## Backend setup

From `backend/`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

For the included local PostgreSQL service, start the container from the project root with `docker compose up -d postgres`. The example database password is for local development only.

Set `DATABASE_URL` to a PostgreSQL URL and set strong, unique `JWT_SECRET_KEY` and `BIOMETRIC_ENCRYPTION_KEY` values in `.env`. Generate an encryption key with:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create the first administrator only after configuring a private one-time `BOOTSTRAP_TOKEN`:

```powershell
alembic upgrade head
python -m app.create_admin --email admin@school.example --name "School Admin"
```

The command prompts for the password and uses the configured bootstrap token. Start the API:

```powershell
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the interactive API docs. To use SQLite locally, set `DATABASE_URL=sqlite:///./smartattend.db`; PostgreSQL is recommended for deployments.

### Face model setup

Recognition is lazy-loaded on the first enrollment or recognition request. Configure `INSIGHTFACE_MODEL` (default `buffalo_l`) and `INSIGHTFACE_ROOT`; install the optional runtime with `python -m pip install -r requirements-ai.txt`. On Windows, InsightFace builds native extensions from source and may require Microsoft C++ Build Tools 14.0+; using Python 3.12 in a supported Linux/WSL environment can simplify setup. If the model or runtime is not installed/available, endpoints return an explicit setup error; attendance is not fabricated. Model weights are not committed. Provision weights only from a trusted source and follow their license.

InsightFace recognition alone is not liveness or anti-spoofing. Automatic marking is disabled by default. To enable it, deploy and validate a server-side liveness checker callable as `module:function`, set `LIVENESS_PROVIDER=module:function`, and only then set `AUTO_MARK_RECOGNIZED=true`. The checker receives image bytes and must return the literal boolean `True` only after its liveness checks pass; otherwise the teacher must confirm the match manually. No liveness implementation/model ships with this project.

## Web setup

From `web/`:

```powershell
npm install
Copy-Item .env.example .env
npm run dev
```

Set `VITE_API_URL` in `.env` if the API is not at `http://localhost:8000/api/v1`. The web camera requires a secure context (`https://` or localhost) and browser camera permission.

## Mobile setup

From `mobile/`:

```powershell
npm install
Copy-Item .env.example .env
npx expo start
```

Set `EXPO_PUBLIC_API_URL` to a backend address reachable from the device. Do not use `localhost` on a physical phone; use a trusted HTTPS endpoint or your development machine's LAN address. Configure Expo camera permissions in `app.json`.

## Database, tests, and reports

```powershell
cd backend
alembic upgrade head
pytest
```

Tests use an isolated SQLite database and do not download or initialize a face model. They cover authentication/role protections, data operations, session attendance, duplicate prevention, and report calculations. The face engine has separate tests for threshold matching, invalid image handling, and clear model-unavailable errors. Real recognition performance must be evaluated using an authorized, representative validation set before production use.

Attendance report CSV is available at `GET /api/v1/attendance/report.csv` to authorized staff. Sessions record a date/time, restrict recognition to enrolled students in the selected class, avoid duplicate records, and calculate absent students on close.

## Configuration still required

- A PostgreSQL database and production-grade secrets.
- A trusted InsightFace model/runtime installation; the model weights are deliberately not committed.
- Parent/guardian consent collection and retention policy appropriate to the jurisdiction.
- A validated liveness/anti-spoofing component before enabling automatic marking.
- HTTPS, restrictive CORS origins, backups, monitoring, and an operational deletion/subject-rights procedure before deployment.
- Expo device/API URL and signing configuration for a distributable Android app.

