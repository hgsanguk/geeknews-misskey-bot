APP=geeknews-misskey-bot

init:
	mkdir -p data
	cp -n .env.example .env || true

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

ps:
	docker compose ps

rebuild:
	docker compose down
	docker compose up -d --build

db:
	docker compose exec $(APP) python - <<'PY'
    import sqlite3
    conn = sqlite3.connect('/data/state.db')
    cur = conn.execute('SELECT COUNT(*) FROM seen_entries')
    print("seen entries:", cur.fetchone()[0])
    conn.close()
    PY