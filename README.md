# flashcard-mcp

대화 중 AI 비서가 MCP로 카드를 저장하고, 폰 브라우저에서 간격 반복으로 복습한다. 설계: [docs/design/0001-flashcard-mcp.md](docs/design/0001-flashcard-mcp.md)

- 웹: `https://flashcard.cloud-handson.com/` (Basic Auth, `.env`의 `FLASHCARD_WEB_AUTH`)
- MCP: `https://flashcard.cloud-handson.com/m/<토큰>/mcp` (Streamable HTTP, URL이 비밀). 토큰은 `.env`의 `FLASHCARD_MCP_TOKENS`에 클라이언트별로 있고(`claude=…,codex=…,chatgpt=…`), 그 라벨이 카드의 `created_by`/`updated_by`에 `mcp:claude`처럼 기록된다. 웹 저장은 `web:<사용자>`.
- 도구: `list_decks`, `search_cards`, `save_card`, `update_card`

## 개발

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
FLASHCARD_DATABASE_URL=sqlite:///flashcard.db FLASHCARD_HOST=127.0.0.1 uv run flashcard init-db
FLASHCARD_DATABASE_URL=sqlite:///flashcard.db FLASHCARD_HOST=127.0.0.1 uv run flashcard serve
# 로컬 MCP: http://127.0.0.1:8810/m/<token>/mcp
```

## 배포 (Mac mini → .208 → .147 nginx)

1. Oracle: ADMIN으로 `sql/001_create_user.sql` 실행(비밀번호는 `.env`의 `FLASHCARD_DB_PASSWORD`와 같게).
2. `uv run flashcard init-db && uv run flashcard check-db`
3. `pm2 start ecosystem.config.cjs && pm2 save`
4. 라우터 192.168.0.208: TCP 8810 → 192.168.5.25:8810 포트포워딩.
5. .147: `deploy/nginx/flashcard.cloud-handson.com.conf`를 `/etc/nginx/conf.d/`에 두고, 처음엔 443 블록을 주석 처리 → `sudo nginx -t && sudo systemctl reload nginx` → `sudo certbot certonly --webroot -w /var/www/html -d flashcard.cloud-handson.com` → 443 블록 활성 → reload.
6. 확인: `curl -s https://flashcard.cloud-handson.com/healthz`

## 운영

- `pm2 restart flashcard` / `pm2 logs flashcard`
- 토큰 추가·교체: `uv run flashcard new-token` → `.env`의 `FLASHCARD_MCP_TOKENS`에 `label=토큰` 추가/교체 → `pm2 restart flashcard` → 해당 커넥터 URL 갱신.
- 기존 테이블에 저장 주체 열이 없으면 `sql/002_add_saved_by.sql` 실행.
- uvicorn 접근 로그는 꺼져 있고 nginx도 `/m/` 접근 로그를 남기지 않는다(URL에 토큰이 있음).
