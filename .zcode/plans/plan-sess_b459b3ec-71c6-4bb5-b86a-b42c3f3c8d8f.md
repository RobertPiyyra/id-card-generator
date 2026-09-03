# ID Card Generator — Improvement & Upgrade Roadmap

You chose all four focus areas with Railway as the deploy target. Because each layer de-risks the next, the plan is ordered **cleanup → tests/CI → refactor → features**. Each phase is independently shippable, so you can stop after any phase and the repo is in a better state.

---

## Phase 1 — Quick-Wins Cleanup (~1 session, low risk)

Ship the in-progress feature and remove the obvious cruft. No architecture changes.

**1.1 Finish the in-progress "DB-backed class dropdown"**
- `dashboard_routes.py:index()` already queries distinct classes and passes `classes=...` to every `render_template("index.html", ...)` ✅ (done)
- Verify ALL `render_template("index.html", ...)` call sites in `index()` receive `classes=` (the diff showed ~5; grep confirms there may be more). Patch any missed.
- Confirm `templates/index.html` and `templates/admin.html` render the "Active Classes in Database" optgroup correctly for both the school-admin and student-facing dropdowns.
- Manually smoke-test: load `/` as admin and as student; confirm dropdown shows DB classes + presets.

**1.2 Remove stray/scratch files**
- Delete `1.txt` (gibberish content: `"dunyA KO ZAROORAT HE ..."` — not project content).
- Leave `scratch/` in place for now but gitignore it (1.3).

**1.3 Harden `.gitignore`**
Current `.gitignore` covers `instance/`, `logs/`, venvs — but is missing several risky entries. Add:
```
# Secrets
.env
.env.*
!.env.example

# Databases (anywhere, not just instance/)
*.db
*.sqlite
*.sqlite3

# Dev scratch & caches
scratch/
.pytest_cache/
.mypy_cache/
.ruff_cache/
1.txt
```
Note: `.env` is currently NOT tracked by git (verified) — adding the rule protects against future accidents. The 12 `*.db` files in `instance/` are already covered by `instance/`.

**1.4 Deduplicate `requirements.txt`**
- Remove the duplicate `Flask-Talisman==1.1.0` line (lines 19–20).

**1.5 Update README "Project Structure" + run command**
- Replace the stale flat layout (`app.py`, `editor_routes.py`, `corel_routes.py`) with the real `app/` package layout.
- Fix run command: `python run.py` (not `python app.py`).
- Note Railway deploy uses `gunicorn -c gunicorn.conf.py "app:create_app()"`.

---

## Phase 2 — Testing & CI Foundation (~1–2 sessions)

Build the safety net *before* the refactor so we can move code with confidence. Your new `TestingConfig` + `conftest.py` are the starting point.

**2.1 Fix the test harness's isolation problem**
- `conftest.py` uses a module-level singleton app (`from app import app`). Combined with `legacy_app.py`'s `_app is None` singleton guard, tests can't get a fresh app. Decouple by allowing `create_app()` to build a fresh app in testing mode (or add a `create_test_app()` helper), so each test session isn't poisoned by import-time `migrate_database()` against the real DB.
- Ensure `FLASK_ENV=testing` route in `get_config()` (just added ✅) actually takes effect before `legacy_app` imports config.

**2.2 Defer the import-time side effects (prerequisite for clean tests)**
- `_cleanup_lost_bulk_jobs()` (line 158) and `migrate_database()` (line 5049) run at import. Wrap them so they only run under `if __name__ == "__main__"` or inside `create_app()` gated on `not TESTING`. This unblocks Phase 2 tests and feeds directly into Phase 3.

**2.3 Write smoke tests (highest ROI first)**
Create `tests/` with:
- `test_routes_smoke.py`: `/` (landing), `/admin/login`, unauthenticated redirect to login, `/api/ai/status` behind admin (302/403), serial-lookup auth guard.
- `test_auth_decorators.py`: parametrize `login_required`/`admin_required`/`super_admin_required`/`school_admin_required`/`student_required` — assert correct redirect vs JSON 403 for each session state and path prefix (`/api`, `/corel`).
- `test_render.py`: render a card with a minimal in-memory template + student; assert output PIL image dimensions match `card_width`×`card_height` and photo is composited.
- `test_serial_batch.py`: create batch → upload fake photo → assert `SerialCard` created with auto-serial; update details → generate → assert PDF bytes returned.

**2.4 Add `ruff` + pre-commit**
- Add `pyproject.toml` (or `ruff.toml`) with a permissive baseline: `E,F,I,UP` selectors, `line-length = 140` (existing code exceeds 120). Don't auto-fix the whole repo on day one — just gate *new* code.
- Add `.pre-commit-config.yaml` running `ruff check` + `ruff format`.
- Run once to fix trivial issues (unused imports, etc.); leave complex warnings as `# noqa`.

**2.5 GitHub Actions CI**
- `.github/workflows/ci.yml`: matrix on push/PR — install deps, `ruff check`, `pytest -q`. Use SQLite + `FLASK_ENV=testing`. No Railway secrets needed.

---

## Phase 3 — Refactor `legacy_app.py` (~2–3 sessions, careful)

The refactor was **already started**: `app/auth_decorators.py` and `app/error_handlers.py` are dormant duplicate copies with comments saying *"To activate: uncomment the import."* The goal is to finish that extraction safely, backed by the Phase 2 tests.

**Coupling facts measured:**
- 14 non-scratch files import from `legacy_app`.
- The public API surface they import: `app`, `db`, `student_bp`, the 5 auth decorators, `log_activity`, `get_default_*_config` (font/photo/qr), `add_template`, `get_template_path/settings/card_size`, `load_font_dynamic`, `load_static_back_template_image`, `verify_fonts_available`, `UPLOAD_FOLDER`, `STORAGE_BACKEND`, `migrate_database`.
- **Three** copies of the decorators exist today: `legacy_app.py` (live), `auth_decorators.py` (dormant), `decorators.py` (used only by serial_batch_routes). Consolidate to one.

**3.1 Consolidate auth decorators to a single source**
- Make `app/auth_decorators.py` the canonical module (it already has the right code).
- Update `legacy_app.py` to `from app.auth_decorators import login_required, admin_required, super_admin_required, school_admin_required, student_required` (re-export for backward compat so the 14 importers keep working).
- Migrate `serial_batch_routes.py` from `app.decorators` → `app.auth_decorators`. Either delete `app/decorators.py` or reduce it to a thin re-export shim. End state: **one** definition of each decorator.

**3.2 Activate the error-handlers extraction**
- Move the 7 `@app.errorhandler(...)` registrations + `add_security_headers` + `rgb_to_hex` filter from `legacy_app.py` into `app/error_handlers.py` as a `register_error_handlers(app)` function (the file already exists with the logic).
- Call `register_error_handlers(app)` from `create_app()`. Remove the originals from `legacy_app.py`.

**3.3 Extract pure helpers out of the monolith**
- The field-layout / template-direction / custom-objects helpers (lines ~580–950) are pure functions with no Flask `app` dependency. Move them to `app/helpers.py` (already 1,953 lines — or a new `app/template_helpers.py`) and re-export from `legacy_app` for compat.

**3.4 Shrink the migration logic**
- `migrate_database()` + `sync_model_columns_to_database()` are ~250 lines inside the monolith. Move to `app/db_migrations.py` (already exists!). Call from `create_app()` gated on `not app.config["TESTING"]`.

**3.5 Target end-state**
`legacy_app.py` becomes a thin module: `create_app()`, blueprint registration, `db` init, and backward-compat re-exports. Target <800 lines (down from 5,104). All route/service files keep working unchanged because the re-exports preserve the import API.

**Refactor safety rule:** every commit in this phase must leave `ruff check` clean and all Phase 2 tests green. No "big bang" commit.

---

## Phase 4 — Feature Upgrades (pick as time allows)

These are independent; do any subset.

**4.1 Activate 2FA for super_admin (scaffolding already exists)**
- Models (`TwoFactorBackupCode`, `UserSession`, `LoginHistory`) + `security_service.py` logic exist but aren't enforced in `auth_routes.py`.
- Add TOTP enrollment flow (scan QR → verify code → store secret), enforce on super_admin login, offer backup codes. Twilio SMS path (`notifications.send_sms`) already works for the SMS channel.

**4.2 Wire SocketIO for real-time progress (or remove it)**
- `flask-socketio` + `collaboration.py` initialize at startup but no template uses it. Decide:
  - **Ship it:** add live progress bars for bulk/serial-batch rendering (emit `render:progress` events; the parallel-render pipeline already has hooks). Useful UX win.
  - **Cut it:** remove `flask-socketio` from requirements + the init block to reduce startup weight. (Lower value, but honest.)

**4.3 Upgrade AI Design Studio with real vision/LLM**
- Current `services/ai_layout.py` is heuristic. Add an optional provider (OpenAI/Anthropic/Gemini vision) gated behind `AI_API_KEY` env var:
  - "Describe your ID card" prompt → template layout.
  - "Upload a photo of an existing ID" → rebuild the template (region detection via the vision model, fall back to MediaPipe face detection already in the photo pipeline).
- Keep heuristic path as the free default; LLM path is opt-in.

**4.4 Public card verification page**
- `verify_routes.py` + `verify.html` exist. Build a public scan-the-QR → validity landing page (shows card validity, photo, revoked/expired status, no PII leak). Strong selling point for schools.

---

## Execution notes
- **Order matters:** Phase 1 → 2 → 3 → 4. Each makes the next safer.
- **Railway target:** no infra changes needed; all improvements are code-level. Docker is intentionally *not* included per your choice.
- **Every phase is independently shippable.** If you want to stop after Phase 1 or 2, the repo is better off than before.
- I'll commit per logical step (not one giant commit) and keep `ruff` + tests green between each.

---

### Suggested first step
Start with **Phase 1** (cleanup + finish the class-dropdown feature) — it's fast, low-risk, and gives immediate visible value while I set up the test harness in Phase 2. Want me to begin there?