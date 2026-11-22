# flashcard-mcp

AI 비서(Claude, ChatGPT, Codex 등)와 대화하다가 MCP 도구로 영어 표현·용어 카드를 저장하고, 폰 브라우저에서 간격 반복(Anki 방식)으로 복습하는 개인용 서버.

- 대화 중: "이 표현 카드로 저장해줘" → 비서가 `save_card` 호출 (영어 교정이나 개념 설명 뒤 스스로 저장하기도 함)
- 복습: 덱 → 학습 → 답 보기 → 다시·어려움·알맞음·쉬움. 영어 덱은 한국어를 먼저 보여 주고 영어를 떠올리게 한다.
- 화면: 하단 탭 덱 / 찾아보기 / 통계(오늘·연속일·30일 막대·자주 틀리는 카드)

한 프로세스(FastAPI)가 웹 화면과 MCP 엔드포인트를 함께 제공한다. DB는 SQLite(기본, 파일 하나)와 Oracle Autonomous DB 중에서 고른다. 설계: [docs/design/0001-flashcard-mcp.md](docs/design/0001-flashcard-mcp.md)

## 빠른 시작 (내 컴퓨터에서만)

준비물: [uv](https://docs.astral.sh/uv/) (Python 3.12는 uv가 받아 온다), git.

```sh
git clone https://github.com/devmrko/flashcard-mcp.git
cd flashcard-mcp
uv sync
uv run flashcard serve
```

- 웹: http://127.0.0.1:8810 (localhost에서는 인증 없음)
- MCP: `http://127.0.0.1:8810/m/local/mcp` (Streamable HTTP)
- 카드는 `./flashcard.db`에 저장된다. 첫 실행 때 테이블을 만든다.

Claude Code에 연결:

```sh
claude mcp add --transport http flashcard http://127.0.0.1:8810/m/local/mcp
```

도구: `list_decks`, `search_cards`, `save_card`, `update_card`. 덱 이름은 자유(소문자)이며 `english`, `it`, `psychology`를 기본으로 표시한다.

## 인증

웹과 MCP는 각각 두 가지 중 고른다. 전체 안내: [docs/guides/auth.md](docs/guides/auth.md). Google 로그인 + MCP 주소 발급의 재사용 명세: [docs/spec/google-signin-mcp-connect.md](docs/spec/google-signin-mcp-connect.md)

| | 기본 | Google |
|---|---|---|
| 웹 `FLASHCARD_AUTH` | `simple`: 아이디·비밀번호 | `google`: 허용한 Google 계정만 ([설정](docs/guides/google-sso.md)) |
| MCP `FLASHCARD_MCP_AUTH` | `token`: 비밀 URL `/m/<토큰>/mcp` | `google`: `/mcp` + 연결할 때 Google 로그인 ([설정](docs/guides/google-sso.md#mcp-커넥터도-google-로그인으로)) |

내 컴퓨터에서만 쓸 때는 설정이 필요 없다. 127.0.0.1 밖으로 열면 웹·MCP 인증이 모두 있어야 서버가 켜진다.

## 폰·원격 커넥터에서 쓰기 (서버 공개)

폰 브라우저나 claude.ai·ChatGPT 원격 커넥터는 HTTPS 주소가 필요하다. 앱은 127.0.0.1에 두고 앞에 리버스 프록시를 둔다.

1. 비밀 값 만들기

   ```sh
   cp .env.example .env && chmod 600 .env
   uv run flashcard new-token     # 클라이언트마다 한 번씩
   ```

   `.env`에 채운다:

   ```sh
   FLASHCARD_PUBLIC_HOST=flashcard.example.com
   FLASHCARD_WEB_AUTH=me:긴비밀번호
   FLASHCARD_MCP_TOKENS=claude=<토큰1>,chatgpt=<토큰2>
   ```

   웹 로그인은 두 가지 중 고른다(MCP는 어느 쪽이든 URL 토큰):
   - `FLASHCARD_AUTH=simple`(기본): 위의 `FLASHCARD_WEB_AUTH` 아이디·비밀번호. 설정이 가장 간단하다. 브라우저 기본 로그인 창이 뜨고 로그아웃은 없다.
   - `FLASHCARD_AUTH=google`: Google 계정으로 로그인, 허용한 이메일만. 폰에서 30일 유지, 로그아웃 있음. Google Cloud에서 OAuth 클라이언트를 만들어야 한다 → [Google 로그인 설정](docs/guides/google-sso.md)

2. 프록시
   - Caddy(인증서 자동): [deploy/examples/Caddyfile](deploy/examples/Caddyfile)
   - nginx + certbot: [deploy/examples/nginx.conf](deploy/examples/nginx.conf)
3. 상시 실행: `pm2 start deploy/examples/pm2.config.cjs && pm2 save` (또는 systemd 등으로 `uv run flashcard serve` 실행)
4. 확인: `curl https://flashcard.example.com/healthz` → `{"status":"ok"}`
5. 커넥터 연결은 두 가지 중 고른다(`FLASHCARD_MCP_AUTH`):
   - `token`(기본): URL `https://flashcard.example.com/m/<토큰>/mcp`. 붙여넣기만 하면 된다. 토큰 라벨이 `created_by`에 `mcp:claude`처럼 남는다.
   - `google`: URL `https://flashcard.example.com/mcp` + 연결할 때 Google 로그인. URL이 새도 안전하고, 허용 목록에서 빼면 그 사람의 커넥터가 바로 막힌다. `created_by`는 `mcp:claude/me@gmail.com`. 웹도 google 로그인이어야 한다 → [Google 로그인 설정](docs/guides/google-sso.md#mcp-커넥터도-google-로그인으로)

주의:
- URL 자체가 비밀번호다. 커넥터 URL을 공유하거나 로그에 남기지 않는다(예시 프록시 설정은 접근 로그를 끈다).
- 웹 로그인(simple 또는 google)과 MCP 토큰 없이 127.0.0.1 밖으로 바인딩하면 서버가 기동을 거부한다.
- 같은 와이파이의 폰에서만 쓸 거라면 Tailscale 같은 사설망으로 대신할 수 있다(이때도 `FLASHCARD_HOST`를 바꾸면 인증 값이 필요하다).

## 설정

모두 `.env` 또는 환경 변수. 전체 목록과 설명은 [.env.example](.env.example).

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `FLASHCARD_HOST` / `FLASHCARD_PORT` | `127.0.0.1` / `8810` | 바인딩 주소 |
| `FLASHCARD_PUBLIC_HOST` | (없음) | 프록시 뒤 공개 도메인. MCP Host 검사에 쓴다 |
| `FLASHCARD_AUTH` | `simple` | 웹 로그인 방식 `simple` 또는 `google` |
| `FLASHCARD_WEB_AUTH` | (없음) | simple: Basic 인증 `user:pass` |
| `FLASHCARD_GOOGLE_CLIENT_ID` / `_SECRET`, `FLASHCARD_ALLOWED_EMAILS`, `FLASHCARD_SESSION_SECRET` | (없음) | google일 때 필수. [가이드](docs/guides/google-sso.md) |
| `FLASHCARD_MCP_AUTH` | `token` | MCP 연결 방식 `token` 또는 `google` |
| `FLASHCARD_MCP_TOKENS` | (없음) | `label=token,...`, 토큰 32자 이상. google 모드에서는 선택(OAuth를 못 하는 클라이언트용) |
| `FLASHCARD_DB` | `sqlite` | `sqlite` 또는 `oracle` |
| `FLASHCARD_SQLITE_PATH` | `flashcard.db` | sqlite 파일 경로(상대 경로는 프로젝트 폴더 기준) |
| `FLASHCARD_DB_USER` / `_PASSWORD` / `_DSN`, `FLASHCARD_WALLET_DIR` | (없음) | oracle일 때 필수 |

명령: `flashcard serve | init-db | check-db | new-token`

### DB 고르기

| | SQLite (기본) | Oracle Autonomous DB |
|---|---|---|
| 맞는 경우 | 혼자 쓰는 서버 한 대 | 이미 ADB가 있거나, 서버가 바뀌어도 데이터를 클라우드에 두고 싶을 때 |
| 준비 | 없음 | ADB 생성, 사용자 생성(`deploy/oracle/create_user.sql`), 월렛 zip 받아 풀기 |
| 설정 | 없음 | `.env`에 `FLASHCARD_DB=oracle`과 접속 값 4개 |
| 백업 | `flashcard.db` 파일 복사 | ADB 자동 백업 |

Oracle 확인: `uv run flashcard init-db && uv run flashcard check-db` → `ok: oracle, fc_card rows = …`

설정이 어긋나면 서버가 켜지지 않는다: `oracle`인데 접속 값이 빠졌거나, `sqlite`인데 `FLASHCARD_DB_DSN`이 남아 있는 경우(빈 SQLite로 조용히 바뀌는 것을 막기 위해).

## 개발

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

화면 공통 스타일·매크로는 [mobile-foundation](https://github.com/devmrko/mobile-foundation)을 git 의존성으로 받는다. 같이 고칠 때는 `pyproject.toml`의 `[tool.uv.sources]`를 잠시 `{ path = "../mobile-foundation", editable = true }`로 바꾼다.

## 폴더 구조

```
src/flashcard/
  cli.py          serve · init-db · check-db · new-token
  config.py       .env 설정과 기동 전 검사
  auth.py         Google 로그인(OpenID Connect)
  oauth.py        MCP 클라이언트용 OAuth 서버(사람 확인은 Google)
  db.py           테이블 정의, SQLite/Oracle 엔진
  store.py        카드·복습 기록 저장소
  srs.py          간격 반복 계산
  stats.py        통계(한국 시간 기준)
  mcp_server.py   MCP 도구 4개
  web.py          웹 라우트 + MCP 마운트
  templates/ static/
tests/            pytest (SQLite)
docs/design/      설계서
docs/guides/      인증 안내(auth.md), Google 로그인 설정(google-sso.md)
docs/spec/        Google 로그인 + MCP 주소 발급 명세(다른 앱에 재사용)
deploy/
  examples/       Caddyfile, nginx.conf, pm2.config.cjs
  oracle/         ADB 사용자 생성 SQL
  cloud-handson/  작성자 배포 구성(참고용)
```

## 한계

- 한 사람용. google 로그인으로 여러 이메일을 허용할 수 있지만 카드는 모두 공유된다(사용자별 덱·통계 없음).
- 하루 새 카드 제한, 되돌리기, 카드 가져오기·내보내기는 없다.
- 비슷한 표현의 중복은 막지 못하고, 앞면이 정확히 같은 카드만 막는다.

## 참고: 작성자 배포 구성

맥 미니(pm2) + Oracle ADB + 별도 nginx 서버(.147)로 운영한다. 설정과 스크립트는 [deploy/cloud-handson/](deploy/cloud-handson/)에 있고, 환경에 맞게 고쳐 쓰는 예시로만 둔다.

- `deploy_mac.sh`: 테이블 생성 → DB 확인 → pm2 시작/재시작 → healthz
- `deploy_nginx.sh`: nginx conf 설치, 인증서 발급(없을 때), reload
- Oracle 스키마: `deploy/oracle/create_user.sql`(ADMIN으로 실행). 저장 주체 열이 없던 옛 테이블 보정은 `migrate_add_saved_by.sql`
- 토큰 교체: `uv run flashcard new-token` → `.env` 수정 → `pm2 restart flashcard` → 커넥터 URL 갱신

## 라이선스

MIT. [LICENSE](LICENSE)
