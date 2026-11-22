# 명세: Google 계정 연동과 MCP 연결 주소 발급

웹 화면과 MCP 서버를 함께 제공하는 개인용 앱에서, Google 계정으로 로그인하고 AI 앱(claude.ai, Claude Code, ChatGPT 등)에 넣을 MCP 주소를 받는 기능의 명세. flashcard-mcp가 참조 구현이고, 다른 앱(FastAPI + MCP Python SDK)에 같은 규칙으로 옮겨 쓴다.

- 참조 구현: `src/flashcard/auth.py`(웹 로그인), `src/flashcard/oauth.py`(MCP 인가 서버), `src/flashcard/web.py`(라우트), `templates/connect*.html`(연결 화면)
- 설계 배경: [docs/design/0002-mcp-oauth.md](../design/0002-mcp-oauth.md)
- 설정 안내(사용자용): [docs/guides/google-sso.md](../guides/google-sso.md)

## 1. 범위

| 포함 | 제외 |
|------|------|
| Google 계정 로그인(웹), 허용 이메일 목록 | 회원 가입, 사용자별 데이터 분리 |
| MCP 클라이언트 자동 등록 + Google 로그인으로 토큰 발급 | Google 외 로그인 공급자 |
| 로그인한 사람에게 MCP 주소와 앱별 연결 방법 표시 | 토큰 주소(`/m/<토큰>/mcp`) 화면 표시·발급 |

## 2. 용어

| 용어 | 뜻 |
|------|----|
| 앱 | 이 명세를 구현하는 서버. 웹 화면과 `/mcp`를 함께 제공 |
| 사람 | 허용 목록에 있는 Google 계정 소유자 |
| 클라이언트 | MCP를 호출하는 AI 앱(claude.ai 커넥터, Claude Code 등) |
| 기준 주소(base URL) | `https://<공개 도메인>`. 공개 도메인이 없으면 `http://localhost:<포트>` |

## 3. 설정 값

| 변수 | 필수 | 규칙 |
|------|------|------|
| `<APP>_AUTH=google` | 예 | 웹 로그인 방식 |
| `<APP>_MCP_AUTH=google` | 예 | MCP 인증 방식. 웹이 google이 아니면 기동 거부 |
| `<APP>_GOOGLE_CLIENT_ID`, `<APP>_GOOGLE_CLIENT_SECRET` | 예 | Google Cloud의 웹 애플리케이션 OAuth 클라이언트 |
| `<APP>_ALLOWED_EMAILS` | 예 | 쉼표 구분, 소문자로 비교 |
| `<APP>_SESSION_SECRET` | 예 | 32자 이상 무작위 값. 바꾸면 모든 웹 세션이 끊김 |
| `<APP>_PUBLIC_HOST` | 공개 시 | 기준 주소, 쿠키 Secure, MCP Host 검사에 사용 |

- 값이 빠지면 기동을 거부하고 빠진 변수 이름을 출력한다.
- 127.0.0.1 밖으로 바인딩할 때 웹·MCP 인증이 없으면 기동을 거부한다.
- Google 콘솔의 승인된 리디렉션 URI는 하나: `<기준 주소>/auth/callback`. 웹 로그인과 MCP 로그인이 같이 쓴다.
- 요청 범위는 `openid email`만. Google 심사 불필요, 동의 화면은 "테스트" 상태 + 테스트 사용자 등록.

## 4. 웹 로그인

| 경로 | 메서드 | 인증 | 동작 |
|------|--------|------|------|
| `/auth/login?next=<경로>` | GET | 없음 | `state`·`nonce` 생성 → 세션에 저장 → Google 인가 화면으로 303 |
| `/auth/callback` | GET | 없음 | 4.1 절차 |
| `/auth/logout` | POST | 로그인 | 세션 삭제, 로그인 화면 표시 |

### 4.1 콜백 절차

1. `state`가 MCP 대기 목록(5.2)에 있으면 MCP 로그인으로 처리하고 끝낸다.
2. 세션의 `state`와 비교(상수 시간). 다르면 400 "로그인 요청이 일치하지 않습니다".
3. 코드 + 클라이언트 보안 비밀번호로 Google 토큰 엔드포인트에서 ID 토큰을 받는다(TLS로 직접 받으므로 서명 검증 대신 TLS 서버 확인을 쓴다, OIDC Core 3.1.3.7).
4. ID 토큰 검사. 하나라도 실패하면 403과 사람에게 보여 줄 문구:
   - `iss`가 `https://accounts.google.com` 또는 `accounts.google.com`
   - `aud`가 우리 클라이언트 ID
   - `exp`가 지나지 않음
   - `nonce`가 세션 값과 같음
   - `email_verified`가 true
   - 소문자 `email`이 허용 목록에 있음
5. 세션을 비우고 `email`만 저장한 뒤 `next`로 303. `next`는 `/`로 시작하고 `//`, `/\`로 시작하지 않는 경로만 허용(외부 이동 차단).

### 4.2 세션과 보호

- 쿠키 `<app>_session`: 서명됨, 30일, `SameSite=Lax`, 공개 도메인이면 `Secure`.
- 모든 웹 경로는 요청마다 세션 이메일이 **현재** 허용 목록에 있는지 본다. 목록에서 빼고 재시작하면 즉시 막힌다.
- 로그인 안 된 GET → `/auth/login?next=<원래 경로>`로 303. POST → 401.
- POST는 `Origin`이 있으면 Host와 같아야 한다(다르면 403).

## 5. MCP 인증 (앱이 OAuth 인가 서버)

Google은 클라이언트 자동 등록을 지원하지 않으므로, 앱이 인가 서버가 되어 자체 토큰을 발급하고 "누구인가" 확인만 Google 로그인에 맡긴다. MCP 인가 사양(2025-06-18)과 MCP Python SDK의 `OAuthAuthorizationServerProvider`를 따른다.

### 5.1 경로

| 경로 | 용도 |
|------|------|
| `/.well-known/oauth-protected-resource/mcp` | 자원 메타데이터(RFC 9728). `resource` = `<기준 주소>/mcp` |
| `/.well-known/oauth-authorization-server` | 인가 서버 메타데이터(RFC 8414). `issuer` = `<기준 주소>/` |
| `/register` | 클라이언트 자동 등록(RFC 7591) |
| `/authorize` | 인가 시작. PKCE `S256` 필수 |
| `/token` | 코드 → 토큰, 갱신 토큰 → 새 토큰 |
| `/revoke` | 토큰 폐기(RFC 7009) |
| `/mcp` | MCP Streamable HTTP. `Authorization: Bearer` 필수, 없으면 401 + `WWW-Authenticate`에 `resource_metadata` |

마운트 순서: 웹 라우트 → 토큰 경로 `/m/<토큰>` → 위 경로들(루트 마운트, 마지막).

### 5.2 연결 절차

```
클라이언트 → /mcp (토큰 없음) → 401 + resource_metadata
클라이언트 → 메타데이터 조회 → /register → client_id
클라이언트 → /authorize (PKCE challenge, redirect_uri, state)
앱: 대기 목록에 {google_state → client_id, 요청 값, nonce, 만료 10분} 저장 → Google 인가 화면으로 302
사람: Google 로그인
Google → /auth/callback?code&state  (state가 대기 목록에 있음)
앱: 4.1의 3~4단계와 같은 검사 → 1회용 코드(5분) 발급 → 클라이언트 redirect_uri?code&state 로 302
클라이언트 → /token (code, code_verifier) → access(1시간) + refresh(30일)
클라이언트 → /mcp (Bearer access)
```

### 5.3 토큰 규칙

| 항목 | 값 |
|------|----|
| access 토큰 | 무작위 32바이트, 1시간 |
| refresh 토큰 | 무작위 32바이트, 30일. 사용 시 회전(옛 access·refresh 쌍 즉시 무효) |
| 인가 코드 | 1회용, 5분, client_id 일치 필요, 메모리에만 |
| 저장 | DB에 SHA-256 해시만. 원문 저장 금지 |
| 검사 | 요청마다 해시 조회 + 만료 + 소유 이메일이 현재 허용 목록에 있는지 |
| 폐기 | access 또는 refresh 하나로 그 쌍 전체 삭제. 만료된 행도 함께 정리 |

### 5.4 남용 제한

등록과 `/authorize`는 로그인 없이 호출되므로 저장량에 상한을 둔다.

| 항목 | 상한 | 초과 시 |
|------|------|--------|
| 등록 클라이언트 수 | 200 | `/register` 400 `invalid_client_metadata` |
| 진행 중 로그인 | 200 | `/authorize` → `temporarily_unavailable` 오류 리디렉션 |

### 5.5 저장 주체

MCP로 만든 데이터의 작성자 기록: `mcp:<클라이언트 이름 slug>/<이메일>` (예: `mcp:claude-code/me@gmail.com`). slug는 소문자·숫자·`-`, 20자 이하, 이름이 없으면 `client`.

### 5.6 DB 테이블

| 테이블 | 열 |
|--------|----|
| `<app>_oauth_client` | `client_id`(PK), `info`(등록 JSON), `created_at` |
| `<app>_oauth_token` | `token_hash`(PK), `kind`(access/refresh), `pair_id`(색인), `client_id`, `subject`(이메일), `scopes`, `resource`, `expires_at`(유닉스 초) |

진행 중 로그인과 인가 코드는 메모리에만 둔다. 재시작하면 진행 중 연결은 처음부터 다시 한다.

## 6. 연결 주소 화면

로그인한 사람이 MCP 주소를 받아 가는 화면. 웹 인증 아래에 둔다.

| 경로 | 내용 |
|------|------|
| 첫 화면 | 목록 행 "AI 커넥터 연결" → `/connect` |
| `/connect` | ① MCP 주소 복사 카드 ② 앱 목록(행을 누르면 상세) ③ 토큰 주소가 설정돼 있으면 라벨만 표시하는 행(링크 없음) |
| `/connect/<앱>` | 그 앱의 연결 단계(번호 목록), 명령어가 있으면 명령어 복사, 주소 복사 |

규칙:
- 표시 주소는 `<기준 주소>/mcp` 하나. 요청 Host 헤더로 만들지 않는다.
- 토큰 주소(`/m/<토큰>/mcp`)는 비밀이므로 어떤 화면에도 출력하지 않는다. 라벨과 개수만 표시.
- 로그인 없이 열면 4.2 규칙대로 로그인 후 같은 화면으로 돌아온다.
- 앱 목록은 코드의 상수 목록(`key`, 이름, 한 줄 설명, 단계, 명령어)으로 관리한다. 모르는 `<앱>`은 404.
- 복사 버튼은 Clipboard API를 쓰고, 막히면 입력란을 선택 상태로 만들고 안내 문구를 바꾼다(`aria-live`).
- 화면 부품은 mobile-foundation 매크로를 쓴다: `copy_field`, `steps(compact=True)`, `row`, `section_title`. 앱 전용 CSS를 새로 만들지 않는다.

앱 목록 기본값:

| key | 이름 | 단계 |
|-----|------|------|
| `claude-ai` | claude.ai (웹·데스크톱·모바일) | 설정 → 커넥터 → 사용자 지정 커넥터 추가 → 이름·주소 입력 → 추가 → 연결 → Google 로그인 |
| `claude-code` | Claude Code | `claude mcp add --transport http <이름> <주소>` → 세션에서 `/mcp` → 이름 선택 → 인증 → Google 로그인 |
| `other` | ChatGPT 등 | 원격 MCP 서버(Streamable HTTP, OAuth) 추가 화면에 주소 입력 → Google 로그인. OAuth를 못 하면 관리자에게 토큰 주소를 받는다 |

## 7. 수용 테스트

참조 구현의 테스트(`tests/test_auth.py`, `tests/test_mcp_oauth.py`, `tests/test_web.py`)가 아래를 확인한다. 다른 앱도 같은 목록을 테스트로 둔다.

- [ ] 로그인 없는 GET은 로그인으로 303, POST는 401
- [ ] state 불일치, aud·iss·exp·nonce·email_verified 실패, 허용 목록 밖 이메일 거부
- [ ] `next`로 외부 주소 이동 불가
- [ ] 메타데이터의 issuer·authorization_endpoint·resource 값
- [ ] 토큰 없는/모르는 토큰의 `/mcp` 401 + `resource_metadata`
- [ ] 등록 → 인가 → 콜백 → 토큰 → 도구 호출 전체 흐름, 저장 주체 기록
- [ ] 인가 코드 재사용 거부, 잘못된 PKCE verifier 거부
- [ ] refresh 회전(옛 토큰 401), revoke 후 401
- [ ] 허용 목록에서 빼면 기존 토큰 401
- [ ] DB에 토큰 원문이 없음
- [ ] 등록·진행 중 로그인 상한
- [ ] `/connect`: 로그인 필요, 주소 표시, 토큰 원문 미출력, 모르는 앱 404
- [ ] 웹 google 없이 MCP google이면 기동 거부

## 8. 다른 앱에 적용하는 순서

1. 설정 클래스에 3절 변수와 기동 검사 추가.
2. `auth.py`(4절)와 `oauth.py`(5절)를 복사하고 DB 테이블 이름·저장 주체 형식만 바꾼다.
3. 웹 라우트에 4절 경로와 인증 의존성, 6절 화면 추가. MCP 앱을 5.1 순서로 마운트.
4. Google 콘솔에 리디렉션 URI 등록, 테스트 사용자 추가.
5. 7절 테스트 통과 확인 후 배포, `curl <기준 주소>/.well-known/oauth-authorization-server`로 확인.
