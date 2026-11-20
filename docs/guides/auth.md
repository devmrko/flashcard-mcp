# 인증

웹 화면과 MCP 커넥터는 인증을 따로 고른다. 내 컴퓨터에서만 쓸 때(`FLASHCARD_HOST=127.0.0.1`)는 아무것도 설정하지 않아도 된다.

## 무엇을 고르나

| | 웹 (`FLASHCARD_AUTH`) | MCP (`FLASHCARD_MCP_AUTH`) |
|---|---|---|
| 간단 (기본) | `simple`: 아이디·비밀번호(브라우저 기본 로그인 창) | `token`: 비밀 URL `/m/<토큰>/mcp` |
| Google | `google`: Google 계정, 허용한 이메일만 | `google`: URL `/mcp` + 연결할 때 Google 로그인 |
| 준비 | `simple`은 없음, `google`은 Google OAuth 클라이언트 | `google`은 웹도 `google`이어야 함 |

권하는 조합:
- 혼자, 빨리 시작: 웹 `simple` + MCP `token`
- 폰에서 편하게, URL 유출 걱정 없이: 웹 `google` + MCP `google` (필요하면 `token`도 함께)

127.0.0.1 밖으로 열 때 웹 인증이나 MCP 인증이 없으면 서버가 켜지지 않는다.

## 웹 `simple` + MCP `token` 설정

```sh
cp .env.example .env && chmod 600 .env
uv run flashcard new-token        # 클라이언트마다 한 번씩 실행
```

`.env`:

```sh
FLASHCARD_AUTH=simple
FLASHCARD_WEB_AUTH=me:긴비밀번호
FLASHCARD_MCP_AUTH=token
FLASHCARD_MCP_TOKENS=claude=<토큰1>,chatgpt=<토큰2>
```

- 웹: 브라우저가 아이디·비밀번호를 묻는다. 로그아웃은 브라우저를 닫거나 저장된 로그인을 지운다.
- MCP 커넥터 URL: `https://<도메인>/m/<토큰1>/mcp`. 라벨(`claude`)이 카드의 저장 주체 `mcp:claude`로 남는다.
- 토큰은 32자 이상, 라벨은 소문자·숫자·`-`. 같은 서비스의 계정이 둘이면 `claude-work=<토큰3>`처럼 하나 더 만든다.

### 토큰 관리

| 할 일 | 방법 |
|------|------|
| 클라이언트 추가 | `new-token` → `.env`의 `FLASHCARD_MCP_TOKENS`에 `라벨=토큰` 추가 → 재시작 → 커넥터에 새 URL 등록 |
| 유출된 토큰 교체 | `new-token` → 해당 라벨의 토큰만 바꿈 → 재시작 → 그 커넥터 URL 갱신. 옛 URL은 바로 404 |
| 비밀번호 변경 | `FLASHCARD_WEB_AUTH` 수정 → 재시작 |

주의: URL 자체가 비밀번호다. 대화·이슈·스크린샷에 붙이지 않는다. 예시 프록시 설정([deploy/examples](../../deploy/examples))은 접근 로그를 남기지 않는다.

## Google 로그인

설정 순서와 문제 해결은 [Google 로그인 설정](google-sso.md):
1. Google Cloud에서 OAuth 클라이언트 만들기 (리디렉션 URI `https://<도메인>/auth/callback`)
2. `.env`에 `FLASHCARD_AUTH=google`, 클라이언트 ID·보안 비밀번호, `FLASHCARD_ALLOWED_EMAILS`, `FLASHCARD_SESSION_SECRET`
3. MCP도 바꾸려면 `FLASHCARD_MCP_AUTH=google` 추가 → 커넥터 URL은 `https://<도메인>/mcp`

## 저장되는 것과 저장되지 않는 것

| 값 | 위치 |
|----|------|
| 비밀번호, MCP 토큰, Google 보안 비밀번호, 세션 비밀값 | `.env`에만 (git에 올라가지 않음) |
| Google 로그인 세션 | 브라우저 쿠키 `fc_session`(서명됨, 30일) |
| MCP OAuth 토큰 | DB `FC_OAUTH_TOKEN`에 SHA-256 해시만 |
| 저장 주체 | 카드의 `created_by`/`updated_by`: `web:<사용자 또는 이메일>`, `mcp:<라벨>`, `mcp:<클라이언트>/<이메일>` |

설계: [0001](../design/0001-flashcard-mcp.md#인증), [0002 MCP OAuth](../design/0002-mcp-oauth.md)
