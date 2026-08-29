# Credit-based Telegram Bot

A production-ready Telegram bot built with `aiogram`, PostgreSQL (Neon), and SQLAlchemy async.

## Features
- Consent-based workflow (Admin approval required for all new users)
- Credit system for running authorized queries
- Recharge requests that must be manually approved by administrators
- Audit logs for credit transactions and search operations

## Instructions

### 1. Create the Bot
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot` and follow the instructions to create a new bot.
3. Copy the **Bot Token** provided.

### 2. Create the Database (Neon)
1. Go to [Neon.tech](https://neon.tech/) and create an account/project.
2. Create a new PostgreSQL database.
3. Copy the async database connection string. Ensure it uses `postgresql+asyncpg://` instead of `postgres://`.

### 3. Setup Environment
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Update `.env` with your Bot Token, Database URL, and your own Telegram User ID (you can find it via `@userinfobot`).

### 4. Running Migrations
Before running the bot, you must set up the database tables:
```bash
alembic upgrade head
```

### 5. Running Locally
Run the bot directly using Python:
```bash
python -m bot.main
```

### 6. Testing
Install testing requirements and run pytest:
```bash
pip install pytest pytest-asyncio aiosqlite
pytest bot/tests/
```

### 7. Deploying to Render
1. Create a new "Background Worker" in Render.
2. Connect your GitHub repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `python -m bot.main`.
5. Under Environment variables, add your `BOT_TOKEN`, `DATABASE_URL`, and `ADMIN_TELEGRAM_IDS`.
