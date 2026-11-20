# 0002 MCP Google 로그인 (OAuth)

작성 2026-11-20. 상태: 구현.

## 목적

MCP 커넥터 연결을 "비밀 URL"이 아니라 Google 로그인으로 한다. URL이 공개돼도 허용 목록 이메일로 로그인하지 않으면 카드를 읽거나 쓸 수 없고, 사람을 허용 목록에서 빼면 그 사람의 모든 커넥터가 즉시 막힌다.

## 범위

- `FLASHCARD_MCP_AUTH=token`(기본, 기존 `/m/<토큰>/mcp`) | `google`.
- `google`이면 `https://<도메인>/mcp`가 OAuth 2.1 보호 자원이 된다. 웹 Google 로그인(`FLASHCARD_AUTH=google`)과 같은 Google 클라이언트·허용 목록을 쓰므로 `FLASHCARD_AUTH=google`이 전제다.
- 경로 토큰(`FLASHCARD_MCP_TOKENS`)은 google 모드에서도 설정돼 있으면 계속 동작한다(OAuth를 못 하는 클라이언트용). 설정하지 않아도 된다.
- 제외: 동의 화면(앱 쪽 "허용" 버튼), 스코프 세분화, 토큰 관리 화면.

## 구조

MCP 클라이언트는 연결할 때 서버에 자동 등록(동적 클라이언트 등록, RFC 7591)하는데 Google은 이를 받지 않는다. 그래서 앱이 인가 서버가 되어 자기 토큰을 발급하고, 사람 확인만 Google에 맡긴다(MCP SDK `OAuthAuthorizationServerProvider`).

```
MCP 클라이언트 ──(1) GET /mcp → 401 + 자원 메타데이터 위치
               ──(2) /.well-known/oauth-protected-resource/mcp, /.well-known/oauth-authorization-server
               ──(3) POST /register (자동 등록)
               ──(4) 브라우저 /authorize?code_challenge=…  → 앱이 Google 로그인으로 보냄
Google ────────(5) /auth/callback?code&state → 앱: Google ID 토큰 검사 + 허용 목록
앱 ────────────(6) 클라이언트 redirect_uri?code=<앱 코드>&state
MCP 클라이언트 ──(7) POST /token (code + PKCE verifier) → 앱 access/refresh 토큰
               ──(8) POST /mcp  Authorization: Bearer <access>
```

- (1)(2)(3)(7)과 PKCE·redirect_uri·코드 만료 검사는 SDK 처리기(`mcp.server.auth`)가 한다. 앱은 저장소와 (4)(5)(6)을 구현한다.
- (5)는 웹 로그인과 같은 `/auth/callback`을 쓴다. `state`가 MCP 대기 목록에 있으면 MCP 흐름, 아니면 웹 흐름. Google 콘솔에 리디렉션 URI를 더 등록하지 않아도 된다.

## 데이터

| 무엇 | 어디 | 수명 |
|------|------|------|
| 등록된 클라이언트 | `FC_OAUTH_CLIENT`(client_id, 메타데이터 JSON) | 영구 |
| access / refresh 토큰 | `FC_OAUTH_TOKEN`(토큰의 SHA-256, 종류, 쌍 id, client_id, 이메일, 만료) | 1시간 / 30일 |
| Google 로그인 대기, 앱 인가 코드 | 프로세스 메모리 | 10분 / 5분 |

- 토큰 원문은 저장하지 않는다(해시만). DB가 새도 토큰으로 쓸 수 없다.
- refresh 시 access·refresh 둘 다 새로 발급하고 옛 쌍을 지운다(회전). 폐기도 쌍 단위.
- 메모리 항목은 재시작하면 사라진다. 그 순간 로그인 중이던 사람만 다시 로그인하면 된다.

## 검사

- 토큰을 쓸 때마다 만료와 허용 목록을 다시 본다. 목록에서 빠진 이메일의 토큰은 바로 거부.
- Google ID 토큰 검사는 웹 로그인과 같은 `auth.check_claims`(iss, aud, exp, nonce, email_verified, 허용 목록).
- 자동 등록은 누구나 할 수 있다. 등록만으로는 아무 권한이 없고, 토큰은 허용 목록 로그인 뒤에만 나온다.
- 저장 주체: `mcp:<클라이언트 이름>/<이메일>`(60자 자름). 클라이언트 이름은 등록 시 `client_name`을 소문자·영숫자로 정리한 것.

## 검증

- 단위: 메타데이터, 등록, authorize → Google(가짜) → 코드 → 토큰(PKCE) → 도구 호출, refresh 회전, 폐기, 허용 목록 제거, 잘못된 토큰 401, 경로 토큰 공존.
- 실제: claude.ai 커넥터에 `https://<도메인>/mcp` 등록 → Google 로그인 → `save_card`.
