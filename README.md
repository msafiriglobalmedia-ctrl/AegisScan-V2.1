# AegisScan AI v2.0.0

**Observe · Detect · Verify · Protect**

AegisScan AI is a responsive, single-operator safety-observation dashboard with a FastAPI backend, SQLite storage, private image uploads, incident review, internal alerts, analytics, CSV export, and an optional YOLO inference adapter.

> **AI transparency:** This project does not include trained model weights. Until you add a hazard model, image submissions are validated and stored but AI inference is not run. The interface clearly reports `AI model not configured`; it never invents detections. A generic object-detection model is not automatically a hazard detector.

## 1. Included features

- Animated AegisScan splash screen with a custom SVG shield/scan emblem, light sweep, orbital glow and reduced-motion support.
- Responsive dark dashboard, mobile navigation and optional light theme.
- Username/password login with signed server-side session cookie.
- Image upload and camera capture (camera access needs HTTPS or localhost).
- Image format verification, 8 MB default upload cap, 30 MP pixel cap, EXIF orientation normalization and sanitized JPEG storage.
- Private image endpoints; upload and annotated image folders are not public static directories.
- Optional local YOLO inference through `AEGIS_MODEL_PATH` and a strict hazard-class allow-list.
- Bounding-box evidence image for supported detections.
- Incident register with `New`, `Under Review`, `Verified`, `False Positive` and `Resolved` states.
- Internal alert queue (not SMS, email or emergency-service delivery).
- Dashboard summary, analytics, model status and CSV incident export.
- SQLite database and persistent data directory suitable for a small single-instance deployment.

## 2. Requirements

- Python 3.10 or later (Python 3.11/3.12 recommended).
- A modern browser.
- Optional: a trained Ultralytics-compatible `.pt` model for real inference.

## 3. Run locally

### Linux / macOS

```bash
cd aegisscan_ai_v2
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and change at least `AEGIS_SECRET_KEY` and `AEGIS_ADMIN_PASSWORD`. Start the server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`.

### Windows PowerShell

```powershell
cd aegisscan_ai_v2
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`, then run:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000
```

### First sign-in

The `.env.example` contains a **placeholder development password**. After copying it to `.env`, replace it before using the application. The default local username is `admin`; the password is the value of `AEGIS_ADMIN_PASSWORD` in `.env`.

## 4. Enable real AI inference

1. Obtain a hazard-detection model trained and evaluated for the safety categories your application is expected to recognize.
2. Create a `models/` folder in the project and copy the model weights into it, for example `models/hazard-vision.pt`.
3. Install the optional inference dependency:

   ```bash
   pip install ultralytics
   ```

4. Configure `.env` using the **exact class names** returned by your trained model. The adapter normalizes spaces and hyphens to underscores when matching labels:

   ```env
   AEGIS_MODEL_PATH=models/hazard-vision.pt
   AEGIS_MODEL_NAME=AegisScan Hazard Vision
   AEGIS_MODEL_VERSION=your-model-version
   AEGIS_MODEL_DEVICE=cpu
   AEGIS_MODEL_CONFIDENCE=0.35
   AEGIS_HAZARD_LABELS=fire,smoke,spill,no_helmet,no_vest,blocked_exit,unsafe_ladder
   ```

5. Restart the application and open **AI Model Center**. It should report `ACTIVE` only after the weights load successfully.

`AEGIS_HAZARD_LABELS` is an allow-list: the model may contain additional classes, but only the names on that list are treated as configured hazard detections. Check spelling and case against your model's class names. For a GPU, configure the device according to the installed PyTorch build and host hardware; `cpu` is the portable default.

### Important model limitations

- The repository contains an adapter, not a trained safety model or a labelled training dataset.
- The configured model must be fit for the intended environment and evaluated on representative images.
- A confidence score is not model accuracy. Evaluate precision, recall, F1-score, confusion matrix and false-negative rates on a separate test set.
- A scan with no detection does **not** prove that a workplace or scene is safe.
- Human review remains necessary. Do not use this prototype as the sole basis for emergency, employment, disciplinary or other high-impact decisions.

## 5. Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `AEGIS_SECRET_KEY` | Signs session cookies; use a long random secret | Development-only placeholder |
| `AEGIS_ADMIN_USERNAME` | Workspace username | `admin` |
| `AEGIS_ADMIN_PASSWORD` | Workspace password | Development-only placeholder |
| `AEGIS_AUTH_ENABLED` | Require login | `true` |
| `AEGIS_COOKIE_SECURE` | Send session cookie over HTTPS only | `false` |
| `AEGIS_DATABASE_PATH` | SQLite database path | `data/aegisscan.db` |
| `AEGIS_UPLOAD_MAX_MB` | Maximum image upload size | `8` |
| `AEGIS_MODEL_PATH` | Local trained weights path | `models/hazard-vision.pt` |
| `AEGIS_MODEL_NAME` | Display name | `AegisScan Hazard Vision` |
| `AEGIS_MODEL_VERSION` | Displayed model version | `2.0.0` |
| `AEGIS_HAZARD_LABELS` | Exact class-name allow-list | Example hazard labels |
| `AEGIS_MODEL_CONFIDENCE` | Inference confidence threshold | `0.35` |
| `AEGIS_MODEL_DEVICE` | Inference device | `cpu` |

For deployment, set environment variables in the hosting provider rather than committing `.env` to source control.

## 6. Deploy on Render

A `render.yaml` Blueprint file is included as a starting point. Before deploying:

1. Set a unique `AEGIS_ADMIN_PASSWORD` and a long random `AEGIS_SECRET_KEY` in the Render service environment.
2. Keep `AEGIS_AUTH_ENABLED=true` and set `AEGIS_COOKIE_SECURE=true` when served over HTTPS.
3. Attach a persistent disk mounted at the configured data directory. SQLite and uploaded evidence are stored there.
4. Deploy a single web instance for this SQLite configuration. Multiple instances need a shared production database and shared object storage.
5. Add model weights through a secure deployment process or private persistent model storage; do not commit private or large weights to a public repository.

The included Render Blueprint uses a persistent disk mount at `/opt/render/project/src/data`. Review the disk plan and available storage on your account before deployment.

## 7. API overview

All endpoints except health and authentication require a signed-in session when authentication is enabled.

| Method | Endpoint | Function |
|---|---|---|
| `GET` | `/api/health` | Application and database health |
| `GET` | `/api/auth/me` | Current session state |
| `POST` | `/api/auth/login` | Sign in (JSON username/password) |
| `POST` | `/api/auth/logout` | Sign out |
| `GET` | `/api/overview` | Dashboard summary |
| `POST` | `/api/scans` | Upload and analyze an image (`multipart/form-data`, field `file`) |
| `GET` | `/api/scans` | Recent scans |
| `GET` | `/api/scans/{id}/image` | Private original image |
| `GET` | `/api/scans/{id}/image?view=annotated` | Private annotated evidence |
| `GET` | `/api/incidents` | Incident list |
| `PATCH` | `/api/incidents/{id}` | Update review status |
| `GET` | `/api/alerts` | Internal alerts |
| `PATCH` | `/api/alerts/{id}` | Acknowledge an alert |
| `GET` | `/api/analytics` | Aggregate workspace statistics |
| `GET` | `/api/model/status` | Inference engine configuration |
| `GET` | `/api/export/incidents.csv` | Download incident CSV |

## 8. Storage and operations

- Database: `data/aegisscan.db`
- Original images: `data/uploads/`
- Annotated images: `data/annotated/`
- Frontend: `static/`

Back up the database and image folders together. Establish a retention policy before storing real workplace images. The current version does not automatically delete old images or records.

## 9. Scope of this release

This is a complete runnable v2 application foundation. The current alert center is internal only; no email/SMS/push integration is included. There is a single administrator account configured through environment variables, not multi-user registration or role-based access. The SQLite configuration is intended for one running instance. Production use should add a managed database, object storage, audit logging, rate limiting, backups, formal security testing and a validated model lifecycle.
