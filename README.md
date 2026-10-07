# Bulk Certificate Generator

A production-style backend for **bulk certificate generation**: create an account, submit
hundreds of recipients at once (JSON **or CSV**), render one PDF per recipient in the
background from a built-in or **uploaded PDF template**, deliver copies by e-mail, and
manage everything afterwards — participants, previous certificates, exports, QR
verification links, revocation and reissue.

Built with **FastAPI + SQLAlchemy + SQLite + ReportLab/PyMuPDF**, zero external
infrastructure (no queue, broker or object store required).

---

## Feature overview

| Area | Features |
|---|---|
| **Bulk jobs** | 202-accepted async jobs, per-certificate progress, partial-failure isolation, retry delivery |
| **Dynamic fields** | Declared field defs (`primary` / `email` / `text` roles, required, visible) or sensible `name + email` defaults |
| **CSV import** | Header-based field inference, optional `column_map`, structured 422 errors before any job is created |
| **Templates** | Upload a PDF containing `{placeholder}` markers; only the placeholders are replaced (exact layout preserved); optional TTF/OTF font, font family/size/colour, e-mail defaults |
| **Placeholders** | `{name}`…dynamic fields, `{event_name}`, `{event_date}`, `{certificate_code}`, `{certificate_id}`, `{verification_url}`, `{recipient}` — shared between PDF and e-mail |
| **Delivery** | Pluggable backends: `outbox` (writes `.eml` files, default), `smtp`, `none`; subject/body templates with placeholders; redeliver failed deliveries |
| **Verification** | Certificate code (`CERT-0007`), secret token, QR code drawn on the certificate, public HTML claim page + JSON API, PDF download via link, revoke/restore with reason |
| **Exports** | ZIP of PDFs, one merged multi-page PDF, ZIP of PNG images, results CSV (codes, statuses, delivery, verify links) |
| **Reissue** | `PATCH` one certificate: fix a typo → PDF regenerated synchronously; optional re-send |
| **Preview** | `POST /previews` renders a sample certificate without creating a job |
| **Auth** | Register/login with bearer tokens (revocable sessions), first user becomes **admin**, admin user management, optional `X-API-Key` machine access |
| **Management APIs** | Jobs list, cross-job certificate search/detail, participant directory with counts, dashboard aggregates |
| **Access control** | Members only see their own jobs/participants/certificates; admins see everything (404s hide existence) |

---

## 1. Setup

Requirements: Python 3.11+

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### Configuration (environment variables, all optional)

| Variable | Default | Purpose |
|---|---|---|
| `CERT_DATABASE_URL` | `sqlite:///./data/certificates.db` | SQLAlchemy database URL |
| `CERT_STORAGE_DIR` | `./data/certificates` | PDFs, templates, fonts, e-mail outbox |
| `CERT_WORKER_CONCURRENCY` | `2` | Parallel background workers |
| `CERT_MAX_RECIPIENTS_PER_JOB` | `1000` | Recipient cap per job |
| `CERT_EMAIL_BACKEND` | `outbox` | `outbox` \| `smtp` \| `none` |
| `CERT_EMAIL_FROM` | `certificates@example.com` | From address |
| `CERT_SMTP_HOST/PORT/USERNAME/PASSWORD/STARTTLS` | – | SMTP settings (required for `smtp`) |
| `CERT_PUBLIC_BASE_URL` | `http://localhost:8000` | Base of verification/QR links |
| `CERT_API_KEY` | *(unset)* | When set, management APIs also accept `X-API-Key` |
| `CERT_ALLOW_REGISTRATION` | `1` | `0` closes `/auth/register` |
| `CERT_TOKEN_TTL_HOURS` | `720` | Bearer token lifetime |
| `CERT_DEFAULT_ID_PREFIX` | `CERT` | Default certificate-number prefix |

## 2. Run

```bash
uvicorn app.main:app --reload
```

Interactive docs: <http://localhost:8000/docs> · Health: `GET /health`

---

## 3. Authentication

```bash
# first account on a fresh instance automatically becomes admin
curl -X POST localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","name":"Me","password":"password123"}'

TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","password":"password123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

- Every management endpoint (jobs, templates, participants, exports, users …) requires
  `Authorization: Bearer <token>` — except `auth/register`, `auth/login`, `/health`
  and the public verification routes (`/api/v1/verify/*`, `/verify/{token}`).
- `X-API-Key: <CERT_API_KEY>` is accepted instead of a user token (machine access, admin role).
- Sessions are revocable: `GET /auth/tokens`, `DELETE /auth/tokens/{id}`, `POST /auth/logout`.
- Roles: `admin` (all data + `GET/POST/PATCH /users`) and `member` (own data only).

---

## 4. API map

Base path: `/api/v1`

### Jobs & certificates
| Method & path | Purpose |
|---|---|
| `POST /certificate-jobs` | Create a job (JSON recipients + field defs) → **202** |
| `POST /certificate-jobs/csv` | Create a job from a CSV file (`payload` = JSON job spec, `column_map` optional) |
| `GET /certificate-jobs` | List jobs (`status`, `search`, `limit`, `offset`) |
| `GET /certificate-jobs/{id}` | Job status, progress counts |
| `GET /certificate-jobs/{id}/certificates` | Per-job certificates (`status`, `delivery_status`) |
| `POST /certificate-jobs/{id}/redeliver` | Retry failed deliveries |
| `GET /certificate-jobs/{id}/download?format=zip\|pdf\|png` | Bulk export |
| `GET /certificate-jobs/{id}/results.csv` | Results report |
| `GET /certificate-jobs/{id}/certificates/{cid}/download` | Single PDF |
| `PATCH /certificate-jobs/{id}/certificates/{cid}` | Fix data (regenerate) + optional `resend` |
| `GET /certificates` | Cross-job search: `email`, `search` (name/mail/code), `status`, `revoked`, `job_id` |
| `GET /certificates/{id}` | Certificate detail |

### Participants & dashboard
| `GET /participants` | Unique participants with certificate/revocation counts (`search`) |
| `GET /participants/{id}` | Participant + all their certificates |
| `GET /dashboard/stats` | Aggregate counters for jobs/certificates/deliveries/participants/templates |

### Templates & preview
| `POST /templates` | Upload PDF template (multipart: `file`, `name`, optional `font`, `email_subject`, `email_body`, `font_family/size/color`) |
| `GET /templates` · `GET /templates/{id}` · `GET /templates/{id}/download` | List / fetch / download |
| `POST /previews` | Render a preview PDF (`template_id?`, `event_name`, `data{}`) — no job created |

### Verification (public)
| `GET /verify/{token}` | JSON status (`issued`/`revoked`) + details |
| `GET /verify/{token}` *(root)* | Public HTML claim/download page |
| `GET /verify/{token}/download` | PDF through the verification link |
| `PATCH /verify/{token}` | Revoke / restore (`{"revoked":true,"reason":"..."}`) |

### Auth & admin
| `POST /auth/register` · `POST /auth/login` · `POST /auth/logout` | Accounts & sessions |
| `GET /auth/me` · `PATCH /auth/me` | Profile / change name + password |
| `GET /auth/tokens` · `DELETE /auth/tokens/{id}` | Session management |
| `GET /users` · `POST /users` · `PATCH /users/{id}` | User administration (admin) |

---

## 5. Typical flows

### JSON job
```bash
curl -X POST localhost:8000/api/v1/certificate-jobs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "event_name": "Advanced Python Workshop",
    "event_date": "2026-09-15",
    "id_prefix": "PYW",
    "qr_enabled": true,
    "send_email": true,
    "fields": [
      {"key": "name",  "label": "Participant", "role": "primary", "required": true},
      {"key": "email", "label": "E-mail",      "role": "email",   "required": true},
      {"key": "cohort","label": "Cohort",      "role": "text",    "visible": true}
    ],
    "recipients": [
      {"name": "Ada Lovelace", "email": "ada@example.com", "cohort": "A"}
    ]
  }'
# -> 202 {"id":"…","status":"queued",…}   poll GET /certificate-jobs/{id}
```

Invalid rows never block the job: they are stored with `status: "failed"` and a reason
(`field 'email' is not a valid e-mail address`, `unknown field 'x'`, …) while valid rows
are generated normally.

### CSV job
```bash
curl -X POST localhost:8000/api/v1/certificate-jobs/csv \
  -H "Authorization: Bearer $TOKEN" \
  -F file=@recipients.csv \
  -F 'payload={"event_name":"K8s Bootcamp","send_email":true}' \
  -F 'column_map={"Full Name":"name","Work Email":"email"}'
```
Headers become fields automatically (e-mail/primary roles detected); `column_map`
bridges arbitrary headers to field keys; unmapped columns are rejected with a 422
*before* anything is created.

### Uploaded template
```bash
curl -X POST localhost:8000/api/v1/templates \
  -H "Authorization: Bearer $TOKEN" \
  -F file=@certificate.pdf -F name="Company Template" \
  -F "email_subject=Your certificate {certificate_code}" \
  -F font=@CustomFont.ttf
```
The template may contain `{name}`, `{event_name}`, `{certificate_code}`, `{qr}`,
`{verification_url}`, … — missing field definitions are rejected at job creation.
Replacement text is fitted to the placeholder box (auto-size) and drawn in the
configured font/colour; everything else is preserved pixel-perfectly.

### Verification & revocation
```bash
curl localhost:8000/api/v1/verify/<token>            # JSON status
open    localhost:8000/verify/<token>                # public claim page (+ QR)
curl -X PATCH localhost:8000/api/v1/verify/<token> \
     -H "Content-Type: application/json" \
     -d '{"revoked":true,"reason":"duplicate issuance"}'
```

---

## 6. Design decisions

- **Two-layer validation** — malformed *requests* fail with 422 up front; malformed
  *rows* are recorded per recipient so one bad address never blocks 999 good ones.
- **Background runner** — a bounded `ThreadPoolExecutor` commits per certificate, so
  clients watch incremental progress; render and delivery failures are isolated.
- **Placeholder replacement** — uploaded templates are *redacted* only under the
  `{key}` text run and the value is drawn back in place (PyMuPDF), keeping the design
  exactly as authored instead of rebuilding the page.
- **Renderer registry** — `services/renderers.py` maps `builtin` / `uploaded-template`
  to renderer functions; new backends register without touching orchestration.
- **Verification tokens are secrets** — unguessable, stored uniquely, returned only in
  the certificate payload/e-mail; they gate the public page, JSON API and downloads.
- **Auth without extra dependencies** — PBKDF2-HMAC-SHA256 password hashes (stdlib),
  opaque bearer tokens stored hashed and revocable, optional shared API key for
  machine-to-machine use.
- **Ownership scoping** — jobs record `created_by`; members are filtered to their own
  data at query level and get 404s for foreign resources (admins see everything).
- **SQLite-friendly aggregation** — participant identity is
  `COALESCE(LOWER(email), 'name:'||name)`, computed in SQL so listing stays fast.

## 7. Project layout

```
app/
  main.py               app factory, routers, lifespan (DB + runner)
  config.py             Settings (env driven)
  database.py           engine/session setup
  models.py             User, AuthToken, Template, CertificateJob, Certificate
  schemas.py            request/response models
  storage.py            on-disk layout helpers
  api/
    common.py           get_db + response mapping
    deps.py             require_user / require_admin / job access
    auth.py             register, login, sessions, profile
    admin.py            user administration
    jobs.py             job creation (JSON/CSV), status, exports, reissue
    management.py       jobs list, certificates, participants, dashboard
    templates.py        template upload/list/download, previews
    verification.py     public verify/revoke/download + HTML claim page
  services/
    jobs.py             job/counts/list services, placeholder coverage checks
    validation.py       per-recipient validation
    csv_import.py       CSV parsing, inference, column mapping
    generation.py       built-in ReportLab certificate renderer
    template_render.py  placeholder replacement in uploaded PDFs
    renderers.py        renderer registry
    runner.py           background processing, delivery, reissue
    email.py            outbox/SMTP delivery backends
    qr.py               QR PNG + verification URLs
    exports.py          ZIP / merged PDF / PNG ZIP / results CSV
    verification.py     token lookup + revocation
    auth.py             password hashing, users, tokens
    management.py       list/search aggregation queries
    templates.py        template + font upload validation
data/                   runtime artifacts (git-ignored)
```
