# EIA Voice — Render Deployment Guide

## Why data was disappearing
Render's free tier uses an **ephemeral filesystem** — every restart wipes all local files including the SQLite database and uploaded images. This version fixes that permanently using:
- **PostgreSQL** (Render free database) — all data persists forever
- **Cloudinary** (free tier) — all uploaded images/videos stored in the cloud

---

## Step 1 — Set up Cloudinary (free)

1. Go to https://cloudinary.com and create a free account
2. From your Cloudinary dashboard, note these three values:
   - **Cloud Name**
   - **API Key**
   - **API Secret**

---

## Step 2 — Set up the Render PostgreSQL database

1. In your Render dashboard → **New** → **PostgreSQL**
2. Choose the **Free** plan
3. Give it a name like `eia-db`
4. After it's created, go to the database page and copy the **External Database URL**
   - It looks like: `postgres://user:password@host/dbname`

---

## Step 3 — Deploy on Render

1. Push this folder to a GitHub repository
2. In Render → **New** → **Web Service** → connect your repo
3. Set these settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`

---

## Step 4 — Add Environment Variables

In your Render web service → **Environment** tab, add these variables:

| Key | Value |
|-----|-------|
| `SECRET_KEY` | Any long random string e.g. `xk92jd8f...` |
| `DATABASE_URL` | The PostgreSQL URL from Step 2 |
| `CLOUDINARY_CLOUD_NAME` | Your Cloudinary cloud name |
| `CLOUDINARY_API_KEY` | Your Cloudinary API key |
| `CLOUDINARY_API_SECRET` | Your Cloudinary API secret |

---

## Step 5 — Deploy

Click **Deploy**. On first boot the app will automatically create all database tables and the superadmin account.

**Default superadmin login:**
- Username: `superadmin`
- Password: `SuperAdmin@EIA2024!`
- ⚠️ Change this password immediately after first login via Edit Profile.

---

## Notes
- The free PostgreSQL on Render expires after **90 days** — upgrade to a paid tier or back up data before then
- Cloudinary free tier gives 25GB storage and 25GB bandwidth/month — plenty for a school
- All uploaded images and videos are now stored permanently on Cloudinary
