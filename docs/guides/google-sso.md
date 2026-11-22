# Google 로그인 설정

웹 화면 로그인을 Basic 인증 대신 Google 계정으로 하게 하는 방법. 허용 목록에 넣은 이메일만 들어올 수 있다. 한 번 로그인하면 30일 동안 유지되고, 첫 화면 아래에 로그아웃 버튼이 생긴다.

MCP 커넥터는 기본으로 `/m/<토큰>/mcp` 주소의 토큰을 계속 쓴다. 커넥터도 Google 로그인으로 바꾸려면 아래 [MCP 커넥터도 Google 로그인으로](#mcp-커넥터도-google-로그인으로)를 이어서 한다.

## 준비물

- Google 계정
- 앱 주소. 둘 중 하나:
  - 공개 서버: `https://flashcard.example.com` (HTTPS 필수, [README](../../README.md#폰원격-커넥터에서-쓰기-서버-공개) 참고)
  - 내 컴퓨터에서만: `http://localhost:8810` (Google은 http를 localhost에만 허용한다. `127.0.0.1`이 아니라 `localhost`로 접속한다)

## 1. Google Cloud 프로젝트 만들기

1. https://console.cloud.google.com 에 로그인한다.
2. 상단 프로젝트 선택 → **새 프로젝트** → 이름(예: `flashcard`) → **만들기**.

## 2. 동의 화면(Google Auth Platform) 설정

1. 메뉴에서 **Google Auth Platform**(이전 이름: API 및 서비스 → OAuth 동의 화면)으로 간다. 처음이면 **시작하기**를 누른다.
2. 앱 정보: 앱 이름 `flashcard`, 사용자 지원 이메일은 내 이메일.
3. 대상(Audience): **외부(External)**.
4. 연락처 이메일을 넣고 정책에 동의 → **만들기**.
5. **대상(Audience)** → **테스트 사용자** → **사용자 추가**: 로그인할 Google 계정 이메일을 모두 넣는다.

게시 상태는 **테스트**로 둔다. 테스트 상태에서는 테스트 사용자로 등록한 계정만 로그인할 수 있어(최대 100명) 개인용으로 맞다. 이 앱은 `openid`, `email` 범위만 요청하므로 Google 심사도 필요 없다.

## 3. OAuth 클라이언트 만들기

1. **클라이언트(Clients)** → **클라이언트 만들기**.
2. 애플리케이션 유형: **웹 애플리케이션**. 이름: `flashcard web`.
3. **승인된 리디렉션 URI**에 추가 (끝의 `/auth/callback`까지 정확히):
   - 공개 서버: `https://flashcard.example.com/auth/callback`
   - 로컬: `http://localhost:8810/auth/callback`
4. **만들기** → 표시되는 **클라이언트 ID**와 **클라이언트 보안 비밀번호**를 복사한다. 보안 비밀번호는 나중에 다시 볼 수 없을 수 있으니 만들 때 JSON을 내려받아 보관한다.

## 4. 앱 설정

`.env`(권한 600)에 추가한다:

```sh
FLASHCARD_AUTH=google
FLASHCARD_GOOGLE_CLIENT_ID=123456-abc.apps.googleusercontent.com
FLASHCARD_GOOGLE_CLIENT_SECRET=GOCSPX-...
FLASHCARD_ALLOWED_EMAILS=me@gmail.com,friend@gmail.com
FLASHCARD_SESSION_SECRET=<아래 명령으로 만든 값>
```

```sh
uv run flashcard new-token    # FLASHCARD_SESSION_SECRET 용 무작위 값
```

- `FLASHCARD_ALLOWED_EMAILS`: 2단계의 테스트 사용자 중 이 앱을 쓸 사람. 대소문자는 구분하지 않는다.
- 공개 서버는 `FLASHCARD_PUBLIC_HOST`가 설정되어 있어야 한다(리디렉션 주소와 쿠키 보안에 쓴다).
- `FLASHCARD_WEB_AUTH`는 google 모드에서 쓰지 않는다.

서버를 다시 시작한다: `pm2 restart flashcard` (또는 `uv run flashcard serve`).

## 5. 확인

1. 앱 주소를 연다 → Google 계정 선택 화면으로 넘어간다.
2. 허용한 계정을 고르면 덱 화면이 나오고, 아래에 내 이메일과 **로그아웃** 버튼이 보인다.
3. 허용하지 않은 계정으로 시도하면 "… 계정은 이 앱을 쓸 수 없습니다"가 나온다.
4. 웹에서 저장한 카드의 저장 주체는 `web:<이메일>`로 기록된다.

## 운영

| 할 일 | 방법 |
|------|------|
| 사람 추가 | 2단계 테스트 사용자에 추가 + `FLASHCARD_ALLOWED_EMAILS`에 추가 → 재시작 |
| 사람 빼기 | `FLASHCARD_ALLOWED_EMAILS`에서 지우고 재시작. 다음 요청부터 막힌다(로그인 상태여도) |
| 모두 로그아웃 | `FLASHCARD_SESSION_SECRET`을 새 값으로 바꾸고 재시작 |
| 보안 비밀번호 유출 | 3단계 클라이언트에서 새 보안 비밀번호 추가 → `.env` 교체 → 재시작 → 옛 값 삭제 |
| Basic 인증으로 되돌리기 | `FLASHCARD_AUTH=simple`, `FLASHCARD_WEB_AUTH=user:pass` → 재시작 |

## MCP 커넥터도 Google 로그인으로

웹 Google 로그인(1~5단계)이 끝난 상태에서 한다. Google 콘솔에 추가할 것은 없다(같은 `/auth/callback`을 쓴다).

1. `.env`에 추가하고 재시작:

   ```sh
   FLASHCARD_MCP_AUTH=google
   ```

   `FLASHCARD_MCP_TOKENS`는 지워도 되고 남겨도 된다. 남기면 `/m/<토큰>/mcp`도 계속 동작한다(OAuth를 지원하지 않는 클라이언트용).
2. 확인: `curl https://flashcard.example.com/.well-known/oauth-authorization-server` → `issuer`, `authorization_endpoint` 등이 나온다.
3. 커넥터 등록. URL은 모두 `https://flashcard.example.com/mcp`(웹에 로그인한 뒤 첫 화면 → **AI 커넥터 연결**에서 복사할 수 있다):
   - claude.ai: 설정 → 커넥터 → 사용자 지정 커넥터 추가 → URL 입력 → **연결** → Google 로그인.
   - Claude Code: `claude mcp add --transport http flashcard https://flashcard.example.com/mcp` → 세션에서 `/mcp` → flashcard → 인증.
   - ChatGPT 등 다른 클라이언트: 원격 MCP 서버에 OAuth를 지원하면 같은 URL로 등록한다. 지원하지 않으면 경로 토큰 URL을 쓴다.
4. 커넥터로 카드를 저장하면 `created_by`가 `mcp:<클라이언트 이름>/<이메일>`로 기록된다.

동작:
- 클라이언트가 앱에 자동 등록하고, 연결 버튼을 누르면 Google 로그인 → 허용 목록 확인 → 앱이 그 클라이언트 전용 토큰(1시간, 갱신 토큰 30일)을 발급한다.
- 토큰은 DB에 해시로만 저장한다. 사람을 `FLASHCARD_ALLOWED_EMAILS`에서 빼고 재시작하면 그 사람의 모든 커넥터 토큰이 바로 거부된다.
- 30일 넘게 쓰지 않으면 다시 연결(로그인)해야 한다.
- 자세한 설계: [docs/design/0002-mcp-oauth.md](../design/0002-mcp-oauth.md)

## 문제 해결

| 증상 | 원인과 조치 |
|------|------------|
| Google 화면에 `redirect_uri_mismatch` | 3단계의 리디렉션 URI가 실제 주소와 다르다. `https`/`http`, 도메인, 포트, `/auth/callback`까지 같은지 확인. 로컬은 `localhost`로 접속해야 한다 |
| `액세스 차단됨` / 앱이 테스트 중 | 그 계정이 2단계 테스트 사용자에 없다 |
| "… 계정은 이 앱을 쓸 수 없습니다" | `FLASHCARD_ALLOWED_EMAILS`에 없다 |
| 로그인 후 다시 로그인 화면으로 돌아옴 | 공개 서버인데 http로 접속했다(쿠키가 HTTPS 전용). https 주소로 연다 |
| "로그인 요청이 일치하지 않습니다" | 로그인 도중 창을 여러 개 열었거나 오래 지났다. 다시 로그인 |
| 서버가 `FLASHCARD_AUTH=google needs …`로 안 켜짐 | 4단계 값이 빠졌다. 메시지에 나온 변수를 채운다 |
| 서버가 `FLASHCARD_MCP_AUTH=google needs FLASHCARD_AUTH=google`로 안 켜짐 | MCP Google 로그인은 웹 Google 설정을 함께 쓴다. 1~4단계를 먼저 한다 |
| 커넥터 연결 중 "로그인 요청이 만료되었습니다" | 10분 안에 로그인을 마치지 못했거나 그 사이 서버가 재시작됐다. 클라이언트에서 다시 연결 |
| 커넥터가 갑자기 401 | 토큰 만료 후 갱신 실패, 또는 허용 목록에서 빠졌다. 다시 연결 |

## 동작 방식 (참고)

OpenID Connect 인가 코드 방식이다. `/auth/login`이 무작위 `state`·`nonce`를 세션에 넣고 Google로 보낸다 → Google이 `/auth/callback?code=…`로 돌려보낸다 → 앱이 `state`를 대조하고, 클라이언트 보안 비밀번호와 함께 코드를 Google 토큰 엔드포인트에 보내 ID 토큰을 받는다 → 발급자·대상(클라이언트 ID)·만료·`nonce`·이메일 확인 여부·허용 목록을 검사한다 → 서명된 세션 쿠키(`fc_session`, 30일, SameSite=Lax)에 이메일을 저장한다. 구현: `src/flashcard/auth.py`, `src/flashcard/web.py`.
