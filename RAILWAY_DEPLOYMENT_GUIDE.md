# Railway Deployment Guide

> **Who this is for:** Anyone deploying this bot to Railway for the first time.
> No prior DevOps or server experience required.
> Follow every step in order. Do not skip steps.

---

## What You Will End Up With

- Your bot running 24/7 on Railway's cloud infrastructure
- MongoDB database managed by Railway
- Pyrogram session file persisting across restarts
- Logs accessible in the Railway dashboard
- Automatic restarts if the bot crashes

---

## Before You Start — Checklist

Complete all of these before opening Railway:

- [ ] You have a **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)
- [ ] You have a **Telegram API ID and API Hash** from [my.telegram.org](https://my.telegram.org)
- [ ] You know the **integer ID** of your source channel (use [@userinfobot](https://t.me/userinfobot))
- [ ] You know the **integer ID** of your target channel or group
- [ ] Your bot is an **admin** in both channels with "Post Messages" permission
- [ ] Your code is **pushed to a GitHub repository** (public or private)
- [ ] The repository contains the `railway.json` and `Dockerfile` files from this project

---

## Part 1 — Create a Railway Account

### Step 1.1

Go to [https://railway.app](https://railway.app) and click **"Start a New Project"**.

### Step 1.2

Click **"Login with GitHub"**. If you don't have a GitHub account, create one first at [github.com](https://github.com).

### Step 1.3

Authorize Railway to access your GitHub account. You only need to do this once.

### Step 1.4

Railway will ask you to verify your email. Check your inbox and click the verification link.

> **Note:** Railway has a free tier but it has usage limits. For a bot running 24/7, you will need the **Hobby Plan ($5/month)**. You can start on the free tier to test, then upgrade.

---

## Part 2 — Create the Project

### Step 2.1

From the Railway dashboard, click the **"New Project"** button (top right).

### Step 2.2

Click **"Deploy from GitHub repo"**.

### Step 2.3

Railway will ask you to choose a repository. Find your bot's repository in the list and click it.

> If your repository doesn't appear, click **"Configure GitHub App"** and grant Railway access to the specific repository.

### Step 2.4

Railway will detect the `Dockerfile` automatically. You will see a screen saying "Configure your service." **Do not deploy yet.** Click **"Add Variables"** or close this dialog — you need to add the database first.

---

## Part 3 — Add MongoDB Database

Your bot needs MongoDB. Railway provides a managed MongoDB service you can add in one click.

### Step 3.1

Inside your Railway project, click the **"+ New"** button (or the **"+"** icon in the canvas area).

### Step 3.2

Click **"Database"** from the dropdown menu.

### Step 3.3

Click **"Add MongoDB"**.

Railway will spin up a MongoDB instance. This takes about 30 seconds. You will see a new green service appear in your project canvas.

### Step 3.4

Click on the **MongoDB service** to open its panel.

### Step 3.5

Click the **"Connect"** tab (or "Variables" tab). You will see a variable called **`MONGO_URL`** or **`MONGODB_URL`**. 

Copy its full value — it looks like:
```
mongodb://mongo:AbcDefGHI123@monorail.proxy.rlwy.net:12345
```

You will paste this into your bot service's environment variables in the next step.

---

## Part 4 — Set Environment Variables

### Step 4.1

Click on your **bot service** (the one built from GitHub, not the MongoDB service).

### Step 4.2

Click the **"Variables"** tab.

### Step 4.3

Click **"RAW Editor"** (this lets you paste all variables at once instead of adding them one by one).

### Step 4.4

Paste all of the following, replacing every placeholder value with your real values:

```
API_ID=your_api_id_here
API_HASH=your_api_hash_here
BOT_TOKEN=your_bot_token_here
MONGO_URI=paste_the_mongodb_url_from_step_3_5_here
SOURCE_CHANNEL_ID=-1001234567890
TARGET_CHAT_ID=-1009876543210
PUBLIC_CHANNEL_LINK=https://t.me/your_public_channel
FIXED_CAPTION=🔥 Premium Content\nJoin: @your_channel
WATERMARK=@your_channel
DAILY_LIMIT=20
SEND_INTERVAL_SECONDS=1800
START_MESSAGE_ID=1
TIMEZONE=UTC
SESSION_DIR=/app/sessions
```

### Step 4.5

Click **"Update Variables"** to save.

> **Important notes:**
> - `SOURCE_CHANNEL_ID` and `TARGET_CHAT_ID` must be **negative integers** starting with `-100`
> - `SEND_INTERVAL_SECONDS=1800` means one post every 30 minutes. Set to `3600` for hourly.
> - `DAILY_LIMIT=20` means 20 posts per day maximum. Adjust to your needs.
> - `START_MESSAGE_ID=1` means scan the entire source channel on first run. If your channel has thousands of messages and you only want recent ones, set this to a recent message ID.

---

## Part 5 — Add a Persistent Volume (Critical for Session Persistence)

Pyrogram stores a `.session` file that keeps your bot authenticated. Without a persistent volume, this file is deleted every time Railway redeploys, requiring re-authentication.

### Step 5.1

Click on your **bot service**.

### Step 5.2

Click the **"Volumes"** tab (it may appear as a storage icon in the sidebar).

### Step 5.3

Click **"Add Volume"** or **"New Volume"**.

### Step 5.4

Set the **Mount Path** to exactly:
```
/app/sessions
```

### Step 5.5

Click **"Create"** or **"Add"**.

Railway will create a persistent disk and mount it at `/app/sessions` inside your container. The Pyrogram session file will be saved here and survive all future restarts and redeployments.

> **Why this matters:** Without this volume, every restart forces the bot to re-authenticate. With the volume, the session file persists forever (until you manually delete it).

---

## Part 6 — Deploy

### Step 6.1

Click on your **bot service**.

### Step 6.2

Click the **"Deploy"** button, or go to the **"Deployments"** tab and click **"Deploy Now"**.

Railway will:
1. Pull your code from GitHub
2. Build the Docker image using your `Dockerfile`
3. Start the container

This process takes 2–4 minutes on first run.

### Step 6.3

Watch the build logs by clicking on the active deployment. You should see:

```
✓ Building Docker image...
✓ Starting container...
INFO: Logging configured.
INFO: Connecting to MongoDB...
INFO: MongoDB connected. Database: 'telegram_bot'
INFO: Ensuring MongoDB indexes...
INFO: Indexes ensured.
INFO: Initializing Pyrogram Client...
INFO: Bot client started.
INFO: Starting initial channel scan...
INFO: Scan progress: 100 messages queued...
INFO: Initial scan complete.
INFO: Scheduler started. Posting interval: 1800s.
```

If you see these messages, the bot is running correctly.

---

## Part 7 — Verify the Bot Is Working

### Step 7.1 — Check logs

Click on your bot service → **"Logs"** tab. You should see activity logs in real time.

### Step 7.2 — Send a test message

Go to your source channel and send any message (photo, video, or text). Within a few seconds, you should see in the logs:

```
INFO: New message received from source channel. ID: 12345
INFO: Queueing single message: 12345
```

### Step 7.3 — Wait for the scheduler

The first scheduled post will fire after `SEND_INTERVAL_SECONDS`. You can temporarily set this to `60` (1 minute) for testing, then change it back to `1800`.

---

## Part 8 — Monitoring

### Viewing Logs

- Railway dashboard → your bot service → **"Logs"** tab
- Logs are live-streamed and searchable
- Filter by keyword (e.g., search "ERROR" to find problems)

### Checking Service Status

- Green dot next to your service = running
- Red dot = crashed (check logs for error message)
- Yellow dot = deploying

### Checking MongoDB

- Click on the MongoDB service → **"Data"** tab
- Or use any MongoDB GUI (MongoDB Compass) with the connection string from Step 3.5

---

## Part 9 — Updating the Bot

When you push new code to GitHub, Railway can redeploy automatically.

### Enable Auto-Deploy

1. Click your bot service → **"Settings"** tab
2. Find **"Auto Deploy"** and ensure it's set to your branch (usually `main`)
3. Now every `git push` will trigger a new deployment automatically

### Manual Redeploy

1. Click your bot service → **"Deployments"** tab
2. Click **"Redeploy"** on the latest deployment

### Zero-Downtime Consideration

Railway stops the old container before starting the new one. During this ~30 second window:
- No new messages from the source channel will be received by the handler
- **This is safe** — any messages sent during this window will NOT be lost because the initial scan logic will catch up on the next restart (the `last_processed_message_id` in MongoDB acts as a cursor)

---

## Part 10 — Rollback

If a new deployment breaks something:

1. Click your bot service → **"Deployments"** tab
2. Find the last working deployment in the list
3. Click the **"..."** menu on that deployment
4. Click **"Rollback"** or **"Redeploy"**

Railway will re-run the older build instantly.

---

## Part 11 — Restarting the Service

To manually restart the bot:

1. Click your bot service
2. Click the **"..."** menu or **"Settings"**
3. Click **"Restart"**

Or click **"Redeploy"** on the current deployment.

---

## Part 12 — Troubleshooting Railway-Specific Issues

### Problem: Deployment fails during build

**Check:** Build logs for the specific error.

Common causes:
- Missing environment variable (bot will fail if any required var is empty)
- `requirements.txt` has a version conflict

**Fix:** Update the variable or fix `requirements.txt`, then redeploy.

---

### Problem: Bot crashes immediately after starting

**Check:** Runtime logs (not build logs — click on the running deployment, then "Logs").

Common causes:
- Wrong `MONGO_URI` (Railway MongoDB URL must be copied exactly)
- Wrong `API_ID` or `API_HASH`
- Bot not admin in channels

---

### Problem: Session keeps getting lost / bot re-authenticates on every restart

**Check:** Volumes tab — ensure the volume is mounted at `/app/sessions`.

If the volume is missing, add it following Part 5 and redeploy.

---

### Problem: Posts stop after some time

**Check:** Logs for "Daily limit reached". If so, this is expected — the bot will resume at midnight.

Also check: `db.queue.countDocuments({ status: "pending" })` in MongoDB. If zero, the queue is empty.

---

### Problem: Items stuck in "processing" status in MongoDB

The bot crashed while sending. Fix via MongoDB:

```javascript
db.queue.updateMany(
  { status: "processing" },
  { $set: { status: "pending", scheduled_at: null } }
)
```

Then restart the bot service.

---

### Problem: Railway shows the service as "sleeping"

This happens on the free tier. Upgrade to the **Hobby Plan** to keep the service running 24/7.

---

## Environment Variable Quick Reference

| Variable | Where to get it | Example |
|---|---|---|
| `API_ID` | [my.telegram.org](https://my.telegram.org) → API Development Tools | `1234567` |
| `API_HASH` | Same page as API_ID | `a1b2c3d4...` |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → /newbot | `123456:ABC...` |
| `MONGO_URI` | Railway MongoDB service → Variables tab → `MONGO_URL` | `mongodb://...` |
| `SOURCE_CHANNEL_ID` | [@userinfobot](https://t.me/userinfobot) in your source channel | `-1001234567890` |
| `TARGET_CHAT_ID` | [@userinfobot](https://t.me/userinfobot) in your target channel | `-1009876543210` |

---

## Getting Channel IDs

1. Add [@userinfobot](https://t.me/userinfobot) to your channel as a member
2. Send any message in the channel
3. The bot replies with the channel's ID
4. It will look like `-1001234567890` — copy the **entire number including the minus sign**

---

*Deployment guide complete. If you encounter an issue not covered here, check the Railway logs first — they contain the exact error message.*