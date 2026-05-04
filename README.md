<div align="center">
 <h1> MediNotes Consultation Studio </h1>
</div>

<br/>

<div align="center">
  <strong>AI-powered consultation documentation for modern healthcare teams.</strong>
  <br />
  Turn raw clinical notes into structured summaries, clear next steps, and patient-friendly email drafts in minutes.
  <br />
  <a href="https://medinotes-studio.web.app/"><strong>Live Production App</strong></a>
</div>

<br />

<div align="center">

![Next.js](https://img.shields.io/badge/Next.js-16-black?logo=nextdotjs)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi)
![TypeScript](https://img.shields.io/badge/TypeScript-Frontend-3178C6?logo=typescript)
![Python](https://img.shields.io/badge/Python-API-3776AB?logo=python)
![Clerk](https://img.shields.io/badge/Auth-Clerk-6C47FF)
![Gemini](https://img.shields.io/badge/AI-Gemini_2.5_Flash-4285F4)

</div>

## Overview

MediNotes Consultation Studio is a healthcare-focused SaaS application that helps clinicians reduce documentation overhead while maintaining professional, consistent, and patient-friendly communication.

<img width="1453" height="600" alt="Screenshot 2026-04-06 at 12 05 15 AM" src="https://github.com/user-attachments/assets/5a0c2aa1-27ef-4532-b1ad-73c1c09f2967" />


The app provides an end-to-end workflow:

1. Capture consultation details (patient, visit date, notes).
2. Generate structured AI output in real time.
3. Store a secure consultation history by authenticated user.
4. Send a polished patient email draft via SendGrid.

## Core Features

- AI consultation report generation using Gemini 2.5 Flash.
- Structured output with three sections:
  - Summary for clinical records.
  - Next steps for provider action.
  - Draft patient email in plain language.
- Live streaming response (Server-Sent Events) for fast feedback.
- Authentication and protected workspace using Clerk.
- Subscription/paywall enforcement with Clerk `Protect` + `PricingTable`.
- Consultation history persisted in SQLite.
- Favorite (pin) important consultations for quick access.
- Search and filter history by patient, email, or visit date.
- Optional notifications via Pushover.
- Optional outbound email delivery via SendGrid.

## Product Experience

### Landing Page

- Modern marketing experience with clear value proposition.
- Sign-in flow powered by Clerk.
- CTA routing authenticated users directly to the product workspace.

### Consultation Workspace

- Form inputs: patient name, date of visit, patient email, consultation notes.
- One-click report generation with streaming output.
- Markdown-rendered consultation report for readability.
- Persistent history sidebar with:
  - Search.
  - Favorites filter.
  - Saved timestamp display.
  - Reloading previous consultations into the editor.


## Architecture

```text
Frontend (Next.js / React / TypeScript)
  -> Auth + UI + SSE client + Markdown render

Backend (FastAPI / Python)
  -> Auth guard (Clerk JWT)
  -> Gemini 2.5 Flash + email tool calling
  -> SendGrid email send
  -> SQLite history persistence
  -> Pushover notification
```

### Key Technical Decisions

- Dual backend entry points:
  - `backend/api/index.py`: serverless/API route deployments.
  - `backend/api/server.py`: containerized deployment.
- Route compatibility fallbacks:
  - Supports both RESTful history routes and query-based compatibility routes (`/api?action=...`) for environments where subpath routing differs.
- Resilient history DB path resolution:
  - Automatically falls back to `/tmp` on read-only serverless filesystems.

## Tech Stack

### Frontend

- Next.js (Pages Router)
- React + TypeScript
- Tailwind CSS v4
- Clerk (`@clerk/nextjs`)
- `react-markdown` + `remark-gfm` + `remark-breaks`
- `react-datepicker`
- `@microsoft/fetch-event-source`

### Backend

- FastAPI
- Uvicorn
- Google Gen AI Python SDK
- `fastapi-clerk-auth`
- SendGrid SDK
- SQLite (`sqlite3`)

## Repository Structure

```text
.
├── backend/
│   ├── api/
│   │   ├── index.py      # Serverless-focused backend entry
│   │   └── server.py     # Container backend entry
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── pages/
│   ├── public/
│   ├── styles/
│   ├── firebase.json
│   └── package.json
├── database/
│   └── consultation_history.sql
├── infra/
│   ├── aws/
│   ├── gcp/
│   └── scripts/
└── docs/
    └── MIGRATION.md
```

## Environment Variables

Keep deployment/runtime values in the repo-root `.env`. For local frontend-only development, either export the `NEXT_PUBLIC_*` values in your shell or create `frontend/.env.local`.

### Required

- `GEMINI_API_KEY` (local/non-GCP development) or Vertex AI auth on GCP
- `GEMINI_MODEL` (defaults to `gemini-2.5-flash`)
- `CLERK_JWKS_URL`
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
- `NEXT_PUBLIC_API_BASE_URL` (Firebase Hosting -> Cloud Run URL)
- `CLERK_SECRET_KEY`

### Optional but Recommended

- `SENDGRID_API_KEY`
- `SENDGRID_SENDER_EMAIL`
- `HISTORY_DB_PATH` (defaults to `consultation_history.db`, with `/tmp` fallback)
- `PUSHOVER_TOKEN`
- `PUSHOVER_USER`

### Infra/Deployment Variables (if used in your pipeline)

- `AWS_ACCOUNT_ID`
- `DEFAULT_AWS_REGION`
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `FIREBASE_PROJECT_ID`
- `VERCEL_OIDC_TOKEN`

## Local Development

### 1. Install dependencies

```bash
npm --prefix frontend install
pip install -r backend/requirements.txt
```

### 2. Configure environment

Set env vars in `.env.local` and `.env`.

When running from shell, you can load the root `.env` first:

```bash
set -a
source .env
set +a
```

### 3. Run frontend

```bash
npm --prefix frontend run dev
```

Frontend runs at:

- `http://localhost:3000`

### 4. Run backend (FastAPI)

```bash
cd backend
uvicorn api.server:app --reload --port 8000
```

Backend runs at:

- `http://localhost:8000`

## Docker Deployment

This repo includes a backend-only Docker build for Cloud Run or App Runner.

### Build

```bash
docker build -f backend/Dockerfile -t medinotes-backend:latest .
```

### Run

```bash
docker run --rm -p 8000:8000 \
  -e GEMINI_API_KEY=<your_gemini_key> \
  -e CLERK_JWKS_URL=<your_clerk_jwks_url> \
  -e CLERK_SECRET_KEY=<your_clerk_secret_key> \
  -e SENDGRID_API_KEY=<optional> \
  -e SENDGRID_SENDER_EMAIL=<optional> \
  medinotes-backend:latest
```

Current production deployment:

- https://medinotes-studio.web.app/

GCP migration notes and Terraform live in [`docs/MIGRATION.md`](docs/MIGRATION.md).

## API Endpoints

### Consultation

- `POST /api/consultation`
  - Auth required (Clerk Bearer token)
  - Request body:

```json
{
  "patient_name": "Jane Doe",
  "date_of_visit": "2026-04-05",
  "patient_email": "jane@example.com",
  "notes": "Patient presents with..."
}
```

  - Response: `text/event-stream` with line-by-line generated content.

### History

- `GET /api/history` or `GET /history`
- `GET /api/history/{history_id}` or `GET /history/{history_id}`
- `PATCH /api/history/{history_id}/pin` or `PATCH /history/{history_id}/pin`

Compatibility fallbacks:

- `GET /api?action=history`
- `GET /api?action=detail&history_id=<id>`
- `PATCH /api?action=pin&history_id=<id>`

### Health

- `GET /health`

## Security and Privacy Notes

- Endpoints are protected with Clerk JWT verification.
- Consultation history is scoped by authenticated `user_id`.
- Avoid storing highly sensitive PHI unless your deployment is configured for regulatory requirements.
- Add encryption at rest, audit logging, and secure key management before production use in regulated environments.

## Known Limitations

- SQLite is suitable for small-scale or single-instance deployments.
- SSE response is line-streamed from final generated text (not token-by-token model streaming).
- Clinical output quality depends on note quality and prompt context.

## Roadmap Ideas

- Multi-provider organizations and team workspaces.
- EHR integration (FHIR/HL7 adapters).
- Role-based access controls and audit trails.
- Human-in-the-loop review workflow before patient email delivery.
- Structured analytics dashboard for documentation efficiency.

## Scripts

```bash
npm --prefix frontend run dev      # Start Next.js in development mode
npm --prefix frontend run build    # Build Next.js
npm --prefix frontend run start    # Start Next.js production server
npm --prefix frontend run lint     # Run ESLint
infra/scripts/deploy_gcp.sh        # Deploy GCP Cloud Run + Firebase Hosting
infra/scripts/destroy_gcp.sh       # Destroy GCP Terraform resources
infra/scripts/deploy_aws.sh        # Deploy AWS App Runner
infra/scripts/destroy_aws.sh       # Destroy AWS Terraform resources
```

## Disclaimer

This application assists with clinical documentation and communication workflows. It does not provide medical diagnosis or treatment recommendations and must not replace clinical judgment.

## Connect

- LinkedIn: https://linkedin.com/in/joshuabalogun
- GitHub: https://github.com/iJoshy

## License

Add your preferred license (MIT, Apache-2.0, or proprietary) in a `LICENSE` file.
