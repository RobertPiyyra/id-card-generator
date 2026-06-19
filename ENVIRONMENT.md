# Environment Configuration

## Quick Start

Copy `.env.example` (if present) to `.env` and fill in real values:

```bash
cp .env.example .env
# Then edit .env with your real secrets
```

**Never commit your real `.env` file** (it's already in `.gitignore`).

## Required Environment Variables

### Flask Core
- `SECRET_KEY` — Long random string for session signing (use `python -c 'import secrets; print(secrets.token_hex(32))'`)
- `FLASK_ENV` — `production` or `development`
- `FLASK_DEBUG` — `0` or `1`

### Database
- `DATABASE_URL` — PostgreSQL URL like `postgresql://user:pass@host:5432/dbname`
  - Falls back to SQLite if not set

### Redis
- `REDIS_URL` — Redis URL like `redis://localhost:6379/0`
  - Used for queue + cache

### Storage Backend
- `STORAGE_BACKEND` — `auto` (default) | `local` | `cloudinary`

If using Cloudinary, also set:
- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`

### Email (SMTP)
- `MAIL_SERVER` — e.g. `smtp.gmail.com`
- `MAIL_PORT` — e.g. `587`
- `MAIL_USE_TLS` — `1` or `0`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`
- `MAIL_DEFAULT_SENDER`

### Google OAuth (optional)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`

### reCAPTCHA (optional)
- `RECAPTCHA_SITE_KEY`
- `RECAPTCHA_SECRET_KEY`

### Razorpay (optional, for payments)
- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`

### Slack (optional)
- `SLACK_WEBHOOK_URL`

### Application URLs
- `APP_BASE_URL` — Internal URL (e.g. `http://localhost:5000`)
- `PUBLIC_BASE_URL` — Public-facing URL

### QR / Barcode
- `QR_DEFAULT_BASE_URL` — Base URL for verification QR codes
### Logging
- `LOG_LEVEL` — `DEBUG` | `INFO` | `WARNING` | `ERROR` (default: `INFO`)

### CORS (optional)
- `CORS_ORIGINS` — Comma-separated allowed origins for API routes (e.g. `https://app.example.com,https://admin.example.com`)
  - If not set, CORS is permissive for API routes only

### Sentry (optional)
- `SENTRY_DSN` — Sentry DSN for error tracking (if not set, Sentry is disabled)
- `SENTRY_ENVIRONMENT` — Environment name (default: `production`)
- `SENTRY_TRACES_SAMPLE_RATE` — Performance trace sample rate 0.0–1.0 (default: `0.1`)

### Database Pool (production)
- `DB_POOL_SIZE` — Connection pool size (default: `10`)
- `DB_MAX_OVERFLOW` — Max overflow connections (default: `20`)
- `DB_POOL_TIMEOUT` — Pool connection timeout in seconds (default: `30`)

### Session
- `SESSION_LIFETIME` — Session lifetime in seconds (default: `3600`)


## Security Notes

- ✅ `.env` is gitignored — your real secrets stay local
- ✅ `.env.example` (if present) shows the format with placeholder values
- ⚠️ Never commit `.env` to git — even once! Rotate credentials if you do
- ⚠️ Don't share `.env` over insecure channels (email, Slack DM, etc.)
- ⚠️ For production, use a proper secrets manager (AWS Secrets Manager, Vault, etc.)

## Verification

To check that your secrets are NOT being committed:

```bash
git check-ignore -v .env           # Should print a .gitignore line
git ls-files | grep -E '^\.env$'    # Should print nothing
git log --all --diff-filter=A --name-only | grep -E '^\.env$'  # Should print nothing
```
