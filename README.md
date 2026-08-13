# THE TAXI COMPANY AI

**UK Taxi / PHV Concierge Assistant — Proof of Concept (POC)**

A premium conversational assistant for journey planning, fare estimates, vehicle selection, policy answers, and member account management. Built as a demonstration of how AI, structured operations data, and policy knowledge can work together for a UK taxi / private hire operator.

> **Important:** Operational data and fares in this repository are **synthetic** and for demonstration only. They are not live tariffs, bookings, or customer records.

---

## What this product does

THE TAXI COMPANY AI helps members:

1. **Plan a journey** — pickup, destination, passengers, and vehicle class  
2. **See eligible vehicles** with images (Sedan, SUV, XL, Executive, and more)  
3. **Receive a fare estimate** from a deterministic pricing engine (not invented by the LLM)  
4. **Ask policy questions** (taxi vs PHV, accessibility, etc.) grounded in stored documents  
5. **Keep trip history** across logins (previous conversations)  
6. **Manage a personal profile** (details, preferences, profile photo)

The experience is designed as a luxury concierge UI: dark theme, gold accents, restrained typography.

---

## Typical member journey

```
Sign up / Sign in
      ↓
New Booking
      ↓
“How much from Heathrow to Westminster?”
      ↓
“How many people will be travelling?”
      ↓
Choose a vehicle (cards with photos)
      ↓
Fare estimate (£ range, distance, duration)
      ↓
Trip saved under Trip History
      ↓
(Optional) Profile — phone, address, preferences, photo
```

Members can reopen a previous conversation from **Trip History** and continue it.  
**New Booking** starts a fresh session without deleting older ones.

---

## Product features

### Conversational booking assistant
- Natural language journey requests  
- Clarifies missing details (passengers, vehicle)  
- Shows vehicle options with capacity and imagery  
- Returns clear fare estimates with a “not a live quote” disclaimer  

### Authentication & membership
- Email + password sign-up and sign-in  
- Secure HTTP-only session cookies  
- Protected app routes (chat, history, profile)  

### Trip History
- Each booking conversation is a separate session  
- Sessions titled automatically from the route when possible (e.g. “Heathrow to Westminster”)  
- Grouped by Today / Yesterday / Earlier (using the browser’s local timezone for display)  
- Full message history restored when a session is opened  

### Member Profile
- View and edit first name, last name  
- Email displayed (read-only in this POC)  
- Optional: phone, date of birth, address, country  
- Optional travel preferences (preferred vehicle, accessibility notes)  
- Profile photo upload (JPEG / PNG / WebP)  
- Home avatar updates after photo save  
- Informational “Identity Verification — coming in a future release” notice (not implemented)  

### Knowledge answers (RAG)
- Policy and reference questions answered from documents already stored in Qdrant  
- The application does **not** rewrite or re-ingest the vector database during normal use  

### Small talk
- Simple greetings (“hi”, “how are you”, “thanks”) are handled in the UI with polite concierge replies  
- Journey and booking questions still go through the backend assistant  

---

## Architecture (overview)

```
Member (React app)
        ↓  HTTPS + cookie session
FastAPI backend
        ├── Auth (users + sessions in Neon PostgreSQL)
        ├── Chat / journey flow (LangGraph orchestration)
        │         ├── Slot filling (LLM + heuristics)
        │         ├── Deterministic pricing engine (Neon)
        │         └── Policy RAG (Qdrant, read-only)
        ├── Trip History (chat_sessions / chat_messages)
        └── Profile (user_profiles + local avatar files)
```

| Layer | Technology | Role |
| --- | --- | --- |
| Frontend | React, Vite, Tailwind | Concierge UI, chat, history, profile |
| Backend | FastAPI | APIs, auth, orchestration |
| Conversation | LangGraph + Groq LLM | Understanding and routing (not fare maths) |
| Pricing | Deterministic Python engine + Neon | Distance, duration, fare bands |
| Structured data | Neon PostgreSQL | Fleet, zones, pricing rules, users, chats, profiles |
| Documents | Qdrant | Policy / reference RAG (read-only) |
| Embeddings | `BAAI/bge-m3` (local, process-wide) | Query encoding for RAG only |

### Design principles used in this POC

- **LLM fills slots; pricing engine calculates fares** — the model does not invent prices.  
- **Auth identity and profile are separate** — `users` for login; `user_profiles` for optional personal details.  
- **Chat history is user-visible transcript only** — LangGraph’s internal state is not stored as the product history.  
- **Secrets stay server-side** — never put Neon / Groq / Qdrant keys in `VITE_*` frontend variables.  

---

## Repository layout

```
.
├── README.md                 # This document
├── .env.example              # Backend / shared env template (no secrets)
├── backend/                  # FastAPI application
│   ├── app/
│   │   ├── api/              # Auth, chat, sessions, profile, health, agents, RAG
│   │   ├── auth/             # Sessions, passwords (Argon2)
│   │   ├── conversation/     # Journey flow, NLU, LangGraph graph
│   │   ├── pricing/          # Deterministic fare engine
│   │   ├── chat_history/     # Trip History persistence
│   │   ├── profile/          # Profile + avatar storage
│   │   ├── rag/              # Qdrant retrieval + embeddings
│   │   ├── db/               # Models, init, repositories
│   │   └── main.py
│   ├── data/uploads/         # Profile images (local POC storage, gitignored)
│   ├── tests/
│   └── requirements.txt
├── frontend/                 # React concierge UI
│   ├── src/
│   │   ├── pages/            # Chat, Profile, Sign-in / Sign-up
│   │   ├── components/       # Chat, layout, vehicles
│   │   ├── services/         # API client (credentials: include)
│   │   ├── context/          # Auth + avatar state
│   │   └── assets/vehicles/  # Vehicle imagery
│   ├── .env.example          # VITE_API_BASE_URL only
│   └── package.json
├── uk_taxi_dataset/          # Synthetic CSVs + policy docs
└── ingest_policies.py        # One-off Qdrant ingest (do not re-run casually)
```

---

## Getting started (local)

### Prerequisites

- Python 3.12+  
- Node.js 20+ (or current LTS)  
- Project-root `.env` filled from `.env.example` (Neon, Groq, Qdrant)  

### 1. Environment

Copy and complete the project-root `.env` (never commit real secrets):

```bash
cp .env.example .env
```

Key variables:

| Variable | Purpose |
| --- | --- |
| `NEON_POSTGRES_STRING` | Neon PostgreSQL connection string |
| `GROQ_API_KEY` / `GROQ_MODEL` | LLM for conversation understanding |
| `QUAD_ENDPOINT` / `QUAD_API_KEY` | Qdrant (existing collection) |
| `QDRANT_COLLECTION` | Default `uk_taxi_policies` |
| `CORS_ORIGINS` | Frontend origins (e.g. `http://localhost:5173`) |

Frontend (public API URL only):

```bash
# frontend/.env.development or frontend/.env
VITE_API_BASE_URL=http://localhost:8000
```

Production builds set `VITE_API_BASE_URL` to the deployed backend (e.g. Render).

### 2. Database schema

Creates tables for taxi data, pricing, auth, chat history, and profiles (safe / idempotent):

```bash
cd backend
source ../venv/bin/activate   # or your virtualenv
pip install -r requirements.txt
python -m app.db.init
```

Optional: load synthetic operational CSVs (see `uk_taxi_dataset/README.md`):

```bash
python -m app.db.import_data
python -m app.db.validate
```

### 3. Backend

```bash
cd backend
source ../venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API docs (local): http://127.0.0.1:8000/docs  

Health checks:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/health/all
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173  

Sign up → use **New Booking** → ask for a fare → open **Trip History** / **Profile** from the sidebar.

---

## Main API surface (authenticated unless noted)

| Area | Endpoints | Notes |
| --- | --- | --- |
| Health | `GET /health`, `/health/postgres`, `/health/qdrant`, `/health/all` | Public liveness |
| Auth | `POST /api/auth/signup`, `/signin`, `/signout`, `GET /api/auth/me` | Cookie session |
| Chat (legacy) | `POST /api/chat` | Still available |
| Trip History | `POST/GET /api/chat/sessions`, `GET /api/chat/sessions/{id}`, `POST .../messages` | Ownership enforced |
| Profile | `GET/PATCH /api/profile`, `POST/DELETE /api/profile/avatar` | Avatar via authenticated file serve |
| RAG / agents | `/api/rag/*`, `/api/agents/*` | Protected operational/policy tools |

All member APIs use the existing session cookie. The backend never trusts a client-supplied `user_id` for ownership.

---

## Pricing & vehicles (how estimates work)

1. Member provides route and passenger count.  
2. System lists **eligible vehicle classes** (capacity / accessibility rules from Neon).  
3. Member selects a class (or only one class applies).  
4. **Pricing engine** estimates distance (POC haversine × road factor), duration, and a **£ min–max** band using fare rules, city and peak modifiers.  

This is explicitly a **POC estimate**, not a live metered quote.

Vehicle presentation in the UI uses local assets mapped by class id (`SEDAN`, `SUV`, `XL`, `EXECUTIVE`, etc.).

---

## Security & privacy (POC posture)

- Passwords hashed with Argon2id  
- Session tokens stored hashed; cookie is HTTP-only  
- Profile images stored on disk under `backend/data/uploads/` (gitignored); DB keeps only a reference  
- Avatar download requires authentication and ownership checks  
- Frontend env may only contain public `VITE_API_BASE_URL`  
- Cross-user access to another member’s chat or avatar returns **404** (no existence leak)  

---

## Testing

```bash
cd backend
source ../venv/bin/activate
pytest                          # full suite (needs Neon where integration tests require it)
pytest tests/test_auth.py -q
pytest tests/test_chat_history.py tests/test_chat_titles.py -q
pytest tests/test_profile.py -q
pytest tests/test_journey_conversation.py -q
```

Frontend typecheck:

```bash
cd frontend
npx tsc --noEmit
```

---

## Deployment notes

| Piece | Guidance |
| --- | --- |
| Backend | Host FastAPI (e.g. Render); set production `.env` secrets |
| Frontend | Build with `VITE_API_BASE_URL=https://<your-api-host>` |
| CORS | Include the production frontend origin in `CORS_ORIGINS` |
| Cookies | Use secure cookies in production (`AUTH_COOKIE_SECURE`) |
| Database | Run `python -m app.db.init` against Neon before first deploy |
| Avatars | Local disk is POC-only; production should use object storage later |

---

## Out of scope / future (intentionally not built)

- Live road routing / real-time quotes  
- Payment capture or booking confirmation to a dispatch system  
- Email change, password reset, MFA  
- Government ID / KYC verification (message only on Profile)  
- Session rename / delete / search / export  
- Corporate accounts, loyalty tiers, multiple addresses  

---

## Dataset disclaimer

The `uk_taxi_dataset/` folder contains **synthetic** UK taxi/PHV operational and pricing data for demos and tests. Do not present it as production client data. Policy markdown used for RAG is reference material for the POC knowledge base.

---

## Support for evaluators

| Goal | What to try |
| --- | --- |
| Happy path booking | Sign up → New Booking → Heathrow to Westminster → passengers → pick SUV → see estimate |
| History | Start a second booking → open Trip History → reopen the first session |
| Profile | Upload a photo → Save → confirm TopNav avatar updates |
| Policy | Ask “What is the difference between a taxi and a PHV?” |
| Graceful chat | Say “hi” / “how are u?” — expect a polite local reply, not a knowledge-base error |

For technical questions about the stack, start with this README, `.env.example`, and interactive API docs at `/docs` when the backend is running.
