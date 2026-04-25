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

## Database Schema

On startup, the app runs a SQLAlchemy additive schema sync after `db.create_all()`. This creates missing tables and adds model columns that older Railway Postgres databases may not have, including `students.back_image_url`.

After pushing a deploy, check Railway logs for messages like:

```text
Added 'back_image_url' column to students
Database migration check completed
```

## Storage

With `STORAGE_BACKEND=cloudinary`, new student photos, generated card images, PDFs, and template URLs are stored in Cloudinary. If Cloudinary credentials are missing, uploads fail loudly instead of falling back to Railway's temporary filesystem.
