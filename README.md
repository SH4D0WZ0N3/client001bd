# 🤖 Telegram Content Automation Bot

A production-grade Telegram bot that automatically copies content from a private source channel, queues it, and publishes it to a target channel or group on a configurable schedule — with full support for photo/video albums, custom captions, daily posting limits, and FloodWait protection.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quick Start (Local)](#quick-start-local)
- [Railway Deployment](#railway-deployment)
- [Environment Variables](#environment-variables)
- [How the Queue Works](#how-the-queue-works)
- [Media Group (Album) Handling](#media-group-album-handling)
- [Daily Limits & Scheduling](#daily-limits--scheduling)
- [Troubleshooting](#troubleshooting)
- [Common Telegram Issues](#common-telegram-issues)
- [MongoDB Reference](#mongodb-reference)

---

## What It Does

| Feature | Detail |
|---|---|
| **Content copying** | Uses Telegram's native `copy_message` — never forwards, no "Forwarded from" label |
| **Album support** | Detects and coalesces multi-photo/video albums before sending |
| **Custom captions** | Appends a fixed watermark/caption to every post |
| **Daily limit** | Stops posting after N posts per day; resets at midnight |
| **Scheduling** | Posts every X seconds (configurable interval) |
| **Queue persistence** | All pending content survives bot restarts via MongoDB |
| **FloodWait handling** | Automatically backs off when Telegram rate-limits the bot |
| **Duplicate prevention** | Database-level unique index prevents the same message being sent twice |

---

## Architecture

```
Source Channel
      │
      │  (new message arrives)
      ▼
Message Handler ──► QueueManager
                         │
                    (media groups buffered
                     3 seconds, then flushed)
                         │
                         ▼
                      MongoDB
                    queue collection
                         │
                    APScheduler
                  (every N seconds)
                         │
                         ▼
                   PostingWorker
                         │
                    TelegramSender
                         │
                         ▼
                  Target Channel/Group
```

### Component Map

```
main.py                  ← entry point, lifecycle manager
app/
├── bot/bot.py           ← Pyrogram client factory
├── database/
│   ├── database.py      ← Motor connection + index creation
│   ├── models.py        ← Pydantic models (QueueItem, State, SentLog)
│   └── repositories.py  ← DB operations (queue, state, sent_logs)
├── handlers/
│   ├── command_handlers.py  ← /start command
│   └── message_handlers.py  ← listens to source channel
├── scheduler/scheduler.py   ← APScheduler setup
├── services/
│   ├── bootstrap.py         ← initial channel scan on first run
│   ├── queue_manager.py     ← media group coalescing + DB insert
│   └── telegram_sender.py   ← copy_message / copy_media_group
├── utils/
│   ├── config.py            ← pydantic-settings env loader
│   └── logging.py           ← loguru configuration
└── workers/posting_worker.py ← scheduled job: dequeue + send
```

---

## Requirements

- Python 3.11+
- MongoDB 6+ (local or Railway plugin)
- A Telegram **Bot Token** from [@BotFather](https://t.me/BotFather)
- Telegram **API ID + API Hash** from [my.telegram.org](https://my.telegram.org)
- The bot must be an **admin** in both the source channel and the target channel/group

---

## Quick Start (Local)

### 1. Clone the repository

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

### 2. Create and activate a virtual environment

```bash
python -m venv venv
source venv/bin/activate       # Linux / macOS
venv\Scripts\activate          # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in every value. See [Environment Variables](#environment-variables) for details.

### 5. Start MongoDB

If you don't have MongoDB running locally:

```bash
# Using Docker (easiest)
docker run -d --name mongo -p 27017:27017 mongo:7
```

### 6. Run the bot

```bash
python main.py
```

On first run, the bot will:
1. Connect to MongoDB and create indexes
2. Scan the source channel from `START_MESSAGE_ID` and queue all existing content
3. Start the scheduler and begin posting

---

## Railway Deployment

See [RAILWAY_DEPLOYMENT_GUIDE.md](docs/RAILWAY_DEPLOYMENT_GUIDE.md) for the full step-by-step guide.

**Summary:**

1. Push code to GitHub
2. Create a Railway project → connect your GitHub repo
3. Add a MongoDB plugin service
4. Set all environment variables (copy from `.env.example`)
5. Add a Railway Volume mounted at `/app/sessions`
6. Deploy

---

## Environment Variables

| Variable | Required | Example | Description |
|---|---|---|---|
| `API_ID` | ✅ | `1234567` | Telegram API ID from my.telegram.org |
| `API_HASH` | ✅ | `abc123...` | Telegram API hash from my.telegram.org |
| `BOT_TOKEN` | ✅ | `123:ABC...` | Bot token from @BotFather |
| `MONGO_URI` | ✅ | `mongodb://...` | Full MongoDB connection string |
| `SOURCE_CHANNEL_ID` | ✅ | `-1001234567890` | Integer ID of the private source channel |
| `TARGET_CHAT_ID` | ✅ | `-1009876543210` | Integer ID of the target channel/group |
| `PUBLIC_CHANNEL_LINK` | ✅ | `https://t.me/...` | Link shown to /start users |
| `FIXED_CAPTION` | ✅ | `Join @channel` | Text appended to every post |
| `WATERMARK` | ✅ | `@channel` | Short watermark tag |
| `DAILY_LIMIT` | ✅ | `20` | Max posts per calendar day |
| `SEND_INTERVAL_SECONDS` | ✅ | `1800` | Seconds between posts (1800 = 30 min) |
| `START_MESSAGE_ID` | ✅ | `1` | Source channel message ID to start scanning from |
| `TIMEZONE` | ✅ | `UTC` | Timezone for daily counter reset |
| `SESSION_DIR` | auto | `/app/sessions` | Set by Dockerfile; override if needed |

---

## How the Queue Works

```
1. New message arrives in source channel
        │
        ▼
2. message_handlers.py receives it
        │
        ├── Single message → inserted into MongoDB queue immediately
        │
        └── Album (media group) → buffered in memory for 3 seconds
                                  then all parts flushed as one queue item
        │
        ▼
3. MongoDB queue collection stores:
   { message_id, media_group_id, message_ids[], status: "pending" }
        │
        ▼
4. APScheduler fires every SEND_INTERVAL_SECONDS
        │
        ▼
5. posting_worker.py dequeues oldest "pending" item
   (atomic find_one_and_update → sets status to "processing")
        │
        ▼
6. TelegramSender copies the message to TARGET_CHAT_ID
        │
        ├── Success → status = "sent", daily_sent_count++
        └── FloodWait → status = "pending" (retry next interval)
        └── Permanent error → status = "failed"
```

### Queue Status Values

| Status | Meaning |
|---|---|
| `pending` | Waiting to be sent |
| `processing` | Currently being sent (atomic lock) |
| `sent` | Successfully delivered |
| `failed` | Permanent failure (message deleted, invalid peer, etc.) |

---

## Media Group (Album) Handling

Telegram delivers album messages as **separate individual updates** with the same `media_group_id`. The bot handles this safely:

1. Each message is added to an in-memory buffer keyed by `media_group_id`
2. A 3-second debounce timer starts on the first message
3. Every subsequent message for the same group **resets** the timer
4. After 3 seconds of no new messages, the entire group is flushed to MongoDB as a single queue item
5. `copy_media_group` sends all parts in one API call, preserving album structure

**Restart safety:** The unique index on `message_id` prevents re-queuing the same message if the bot restarts mid-album.

---

## Daily Limits & Scheduling

- The `posting_worker` runs on every scheduler tick (`SEND_INTERVAL_SECONDS`)
- At the start of each tick, it checks if `last_reset_date` matches today's date
- If not (new day), it resets `daily_sent_count` to 0
- If `daily_sent_count >= DAILY_LIMIT`, the tick exits early — no post is made
- The counter increments atomically in MongoDB after each successful send

---

## Troubleshooting

### Bot starts but nothing is posted

1. Check that the bot is an **admin** in both the source and target channel
2. Confirm `SOURCE_CHANNEL_ID` and `TARGET_CHAT_ID` are correct integer IDs (negative numbers starting with `-100`)
3. Check MongoDB queue: `db.queue.find({status: "pending"})` — if empty, the initial scan may not have run
4. Check logs for `"Initial scan already completed"` — if so, the bot thinks it already scanned

**Reset to re-scan:**
```javascript
// In MongoDB shell or Compass
db.state.deleteOne({ _id: "main_state" })
```
Then restart the bot.

### "FloodWait" in logs

Normal behavior. Telegram is rate-limiting sends. The bot automatically waits and retries. No action needed.

### Session file error / authentication loop

The Pyrogram `.session` file is missing or corrupted. On Railway:
1. Go to your service → Volumes → verify the volume is mounted at `/app/sessions`
2. If the session file is missing, the bot will re-authenticate automatically using the bot token (no manual action needed for bots)

### Items stuck in "processing" status

Indicates the bot crashed mid-send without updating status. Fix:
```javascript
db.queue.updateMany(
  { status: "processing" },
  { $set: { status: "pending", scheduled_at: null } }
)
```

---

## Common Telegram Issues

| Error | Cause | Fix |
|---|---|---|
| `FloodWait` | Too many API calls | Bot handles automatically. Increase `SEND_INTERVAL_SECONDS` to reduce frequency |
| `MessageIdInvalid` | Message was deleted in source channel | Bot marks as `failed` and continues |
| `ChannelInvalid` | Bot not in channel, or wrong ID | Add bot as admin to both channels |
| `PeerIdInvalid` | Wrong chat ID format | Use integer IDs from @userinfobot, not usernames |
| `ChatAdminRequired` | Bot lacks permissions | Make bot admin with "Post Messages" permission |

---

## MongoDB Reference

### Collections

| Collection | Purpose |
|---|---|
| `queue` | Pending, processing, sent, and failed message items |
| `state` | Single document tracking `last_processed_message_id` and `daily_sent_count` |
| `sent_logs` | Historical log of every successfully sent message |
| `apscheduler.jobs` | APScheduler job persistence across restarts |

### Useful Queries

```javascript
// See pending items count
db.queue.countDocuments({ status: "pending" })

// See today's sent count
db.state.findOne({ _id: "main_state" })

// See failed items
db.queue.find({ status: "failed" }).sort({ created_at: -1 }).limit(20)

// Retry all failed items
db.queue.updateMany(
  { status: "failed" },
  { $set: { status: "pending", error_message: null, retry_count: 0 } }
)

// View sent log
db.sent_logs.find().sort({ sent_at: -1 }).limit(10)
```

---

## License

MIT — use freely, modify as needed.