# 0001 Flashcard MCP + 모바일 복습 앱

작성 2026-11-19. 상태: 초안(구현과 함께 갱신).

## 목적

대화(음성 영어 회화, IT 학습) 중에 AI 비서가 MCP 도구로 카드를 저장하고, 출퇴근 중 폰 브라우저에서 간격 반복으로 복습한다.

## 범위 (v1)

- MCP 도구 4개: `list_decks`, `search_cards`, `save_card`, `update_card`.
- 웹: 덱 목록 → 덱(복습 시작·카드 목록·검색·직접 추가) → 카드 상세(수정·삭제). 복습 화면은 앞면 → "답 보기" → 모름/애매/앎.
- 덱은 자유 문자열(소문자 slug). 기본 표시: `english`, `it`, `psychology`.
- 제외: 임베딩 유사도 검색, 통계 화면, 다중 사용자, OAuth.

## 구조

```
폰 브라우저 / AI 비서(MCP 클라이언트)
  → Cloudflare → nginx(192.168.0.147, TLS)
  → 192.168.0.208:8810 (리피터 포트포워딩)
  → Mac mini 192.168.5.25:8810  (uvicorn, pm2)
  → Oracle ADB (vector-knowledge와 같은 인스턴스, 전용 스키마 FLASHCARD, 월렛 thin 모드)
```

한 FastAPI 프로세스가 웹(`/`)과 MCP(`/m/<token>/mcp`, Streamable HTTP, stateless, JSON 응답)를 함께 제공한다.

## 인증

- 웹: 앱 자체 HTTP Basic(`FLASHCARD_WEB_AUTH=user:pass`). nginx는 TLS·프록시만 한다(receipe·conduct-web과 같은 방식).
- MCP: URL 경로의 비밀 토큰. 클라이언트·계정마다 따로 발급한다(`FLASHCARD_MCP_TOKENS=claude=…,codex=…,chatgpt=…`, 각 32자 이상). ChatGPT·Claude 원격 커넥터는 OAuth 아니면 무인증만 지원하므로 헤더 토큰 대신 경로 토큰을 쓴다. URL 자체가 비밀번호이므로 로그·공유에 주의한다.
- 설정이 없으면 기동을 거부한다(무인증 노출 방지). 예외: 바인딩이 127.0.0.1이고 웹 인증이 비어 있으면 로컬 개발로 본다.
- MCP 전송 보안: Host 허용 목록(공개 도메인, localhost) 검사를 켠다.

## 데이터

`FC_CARD` 한 테이블. `(deck, front_key)` 유일. `front_key`는 앞면을 소문자·공백 정리한 값으로 중복 저장을 막는다.

| 열 | 설명 |
|----|------|
| id | identity PK |
| deck | 덱 slug |
| front / back | 앞면(영어 표현·용어) / 뒷면(뜻·설명) |
| example, note | 예문, 메모(교정 전 표현 등) |
| source | `mcp` / `web` |
| created_by, updated_by | 저장·마지막 수정 주체. MCP는 `mcp:<토큰 라벨>`, 웹은 `web:<Basic 사용자>` |
| ease, interval_days, reps, lapses, due_at | 간격 반복 상태 |
| created_at, updated_at | UTC (naive, 앱에서 계산) |

시간은 모두 앱이 계산한 UTC 값을 바인딩한다. DB의 `SYSTIMESTAMP`는 쓰지 않는다(세션 시간대 영향 제거).

## 간격 반복 (SM-2 단순화)

- 모름: reps=0, lapses+1, ease−0.2, 10분 뒤 다시.
- 애매: ease−0.15, interval = max(1, interval×1.2)일.
- 앎: reps 0→1일, 1→3일, 이후 interval×ease일. reps+1.
- ease 하한 1.3. 새 카드는 저장 즉시 복습 대상.

## 공통 기반 재사용

- UI: `mobile-foundation`(경로 의존, receipe와 같은 방식).
- DB: python-foundation `create_database`는 월렛 `connect_args`를 받지 않아 vector-knowledge `knowledge_app/database.py`와 같은 방식으로 엔진을 만든다. → 후속: 월렛 엔진 생성기를 python-foundation에 올리는 것을 검토(현재 두 곳 중복).

## 검증

- 단위: SRS 계산, 저장소(SQLite), 웹 인증/라우트, MCP 도구 호출(인메모리 클라이언트).
- 실제: Oracle 스키마 생성 후 `flashcard check-db`, 공개 URL에서 MCP `tools/list`, 폰 브라우저 복습.

## 저장 주체 기록

MCP 원격 커넥터는 Claude·ChatGPT 계정 정보를 서버에 보내지 않는다. 그래서 "어느 토큰으로 들어왔는가"를 주체로 기록한다.
토큰마다 마운트(`/m/<token>`)를 따로 두고, 마운트 래퍼가 ASGI scope에 라벨을 넣으면 도구가 `ctx.request_context.request.scope`에서 읽는다.
라벨은 설정에서 정하므로 신뢰할 수 있다(모델이 인자로 자기 신고하는 방식은 쓰지 않는다). stateless 모드라 MCP `initialize`의 clientInfo는 도구 호출 시점에 남아 있지 않아 쓰지 않는다.
한 서비스의 계정이 둘이면 라벨을 `claude-work`처럼 나눠 토큰을 하나 더 발급한다.

## 한계

- 단일 사용자. 경로 토큰이 유출되면 카드 쓰기가 가능하다(토큰 교체로 대응).
- 임베딩 검색 없음: 비슷한 표현의 중복은 막지 못하고 정확히 같은 앞면만 막는다.
