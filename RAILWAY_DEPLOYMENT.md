# Railway Deployment

This app uses SQLAlchemy for the database and Cloudinary for durable uploaded/generated files.

## Required Railway Variables

Set these in the Railway service variables:

```text
DATABASE_URL=<Railway Postgres connection URL>
STORAGE_BACKEND=cloudinary
CLOUDINARY_CLOUD_NAME=<your Cloudinary cloud name>
CLOUDINARY_API_KEY=<your Cloudinary API key>
CLOUDINARY_API_SECRET=<your Cloudinary API secret>
SECRET_KEY=<a long random secret>
```

If you use bulk/background jobs, also attach Redis and set:

```text
REDIS_URL=<Railway Redis connection URL>
```

The app no longer falls back to `localhost:6379`. If `REDIS_URL` is missing or Redis is temporarily unreachable, Redis caching and RQ queueing are skipped and the app continues running.

Use the private `redis.railway.internal` URL only in the Railway app service that lives in the same project and environment as the Redis service. That hostname will not work from your laptop or from a different Railway project/environment. For local testing, use Railway's public TCP proxy URL/`REDIS_PUBLIC_URL` instead.

## MediaPipe System Packages

MediaPipe needs native OpenGL/GLib libraries on Railway. `nixpacks.toml` installs these apt packages:

```text
libgl1
libglib2.0-0
libsm6
libxext6
libxrender1
libgomp1
```

These packages fix errors like:

```text
libGL.so.1: cannot open shared object file
```

## Database Schema

On startup, the app runs a SQLAlchemy additive schema sync after `db.create_all()`. This creates missing tables and adds model columns that older Railway Postgres databases may not have, including `students.back_image_url`.

After pushing a deploy, check Railway logs for messages like:

```text
Added 'back_image_url' column to students
Database migration check completed
```

## Storage

With `STORAGE_BACKEND=cloudinary`, new student photos, generated card images, PDFs, and template URLs are stored in Cloudinary. If Cloudinary credentials are missing, uploads fail loudly instead of falling back to Railway's temporary filesystem.

The app also creates missing fallback static images at startup:

```text
static/placeholder.jpg
static/photo_placeholder.png
static/logos/qr_placeholder.png
```
