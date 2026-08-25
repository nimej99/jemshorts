# FORK_NOTES

promo-shorts 는 [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) (MIT)의
포크다. upstream remote 는 유지하며, 작업 브랜치는 `v1-dev` 다.
이 문서는 포크 운영 원칙과 upstream 대비 변경 사항의 단일 장부(ledger)다.

## 원칙

1. **MPT 코어 수정 0이 기본.** 신규 코드는 전부 `app/promo/` (테스트는
   `test/promo/`) 하위에 둔다. 코어 동작 변경이 필요하면 코어 파일을 고치는
   대신 래핑/매핑 계층(예: `app/promo/configmap.py`)으로 우회한다.
2. **불가피한 코어 수정은 반드시 이 문서에 등재한다.** 항목마다
   사유, 대상 파일, diff 위치(커밋 해시 또는 라인 범위)를 기록한다.
   등재 없는 코어 수정은 리뷰에서 반려한다.
3. **upstream cherry-pick 원칙.**
   - upstream 은 정기적으로 `git fetch upstream` 으로만 추적한다.
   - 통째 merge 대신 필요한 커밋만 `git cherry-pick -x` 로 가져온다
     (`-x` 로 원본 커밋 해시를 메시지에 남긴다).
   - cherry-pick 이 아래 "코어 수정 예외" 또는 "허용된 리소스 수정"과
     충돌하면, 포크 측 변경을 우선하고 충돌 해소 내용을 이 문서에 추가한다.

## 허용된 리소스 수정 (코어 수정으로 취급하지 않음)

| 대상 | 사유 |
| --- | --- |
| `resource/songs/` | 기본 BGM 트랙 팩 교체 (M0). 코드가 아닌 번들 리소스이며, 파일명 규약만 유지하면 코어 로직에 영향 없음 |
| `docker-compose.yml` | 로컬 배포 구성을 단일 `app` 서비스로 재작성 (M0). upstream 의 webui/api 2컨테이너 구성을 대체. `docker-compose.release.yml`, `docker-compose.gpu.yml` 은 손대지 않음 (release 는 후속 마일스톤) |
| `docker-compose.yml` (M0 docker boot) | 볼륨 마운트 대상 오류 수정: `/app/*` → `/MoneyPrinterTurbo/*` (이미지 WORKDIR 기준. 기존 경로는 코어 `storage_dir()` 와 연결되지 않아 영속화가 동작하지 않음). `command` 를 `scripts/docker-entrypoint.sh` 로 교체해 기동 전 `ensure_config()` 훅 연결 |
| `scripts/docker-entrypoint.sh` | 신규 (M0). 단일 컨테이너 엔트리포인트: `ensure_config(/MoneyPrinterTurbo/storage)` 호출 후 api(백그라운드) + webui(포그라운드) 기동 |
| `scripts/docker-entrypoint.sh` (M0 review fixes) | 경로를 셸 보간 대신 env(`PROMO_STORAGE_DIR`)로 파이썬에 전달하고, `MPT_CONFIG_FILE` 을 export 해 코어가 영속 config 를 직접 사용하게 함 |
| `resource/fonts/NotoSansKR-Bold.otf` | 한국어 기본 폰트 추가 (M0). OFL 1.1 라이선스, 출처: [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk). 코드가 아닌 번들 리소스 |
| `.gitignore` | append 만: `data/`, `bridge-secret/` (compose 볼륨 디렉터리, M0) |
| `.gitignore` (biz/commerce-picks) | append 만: `.gjc/` — GJC 에이전트 런타임 세션 상태(토큰 로그·게이트). PUBLIC 레포에 커밋되면 유출이라 즉시 제외 |
| `.dockerignore` | append 만: `data/`, `bridge-secret/` — `COPY . .` 시 브리지 시크릿/로컬 데이터가 이미지에 구워지는 것을 방지 (M0) |
| `.dockerignore` (biz/commerce-picks) | append 만: `.gjc/` — 에이전트 세션 상태가 이미지에 구워지는 것을 방지 |
| `docker-compose.postiz.yml` | 신규 — Postiz 셀프호스트 스택 (게시 백엔드). 앱 컨테이너와 분리된 별도 구성 |
| `dynamicconfig/development-sql.yaml` | 신규 — temporal 동적 설정 번들 (postiz 공식 레포 원본) |
| `.gitignore` (postiz 스택) | append 만: `docker-compose.postiz.env` — JWT 시크릿·관리자 계정·API 키 저장소 |

## 코어 수정 예외 (등재 필수)

| 파일 | 사유 | 내용 / diff 위치 |
| --- | --- | --- |
| `app/config/config.py` | config 영속화 (M0 review fixes). symlink 우회 방식은 심볼릭 링크 미지원 파일시스템·컨테이너 재빌드 시 깨지기 쉬워 아키텍트 리뷰에서 반려됨. env 오버라이드가 최소·명시적 해법 | `config_file = os.environ.get("MPT_CONFIG_FILE", f"{root_dir}/config.toml")` 1줄 + `save_config()` 의 임시파일 디렉터리를 `os.path.dirname(config_file)` 로 변경(볼륨 경계에서 `os.replace` 원자성 유지). load/save 모두 같은 `config_file` 경로 사용 (v1-dev, M0 review fixes 커밋) |
| `app/router.py` | promo API 노출 (M2). promo 라우터(`/api/v1/promo/*`)를 앱에 등재하려면 루트 라우터 include 가 유일한 진입점 — 래핑 우회 불가 | import 1줄 + `root_api_router.include_router(promo_api.router)` 1줄 (v1-dev, M2 커밋). 라우터 구현 전체는 `app/promo/api.py` 에 격리 |

## 참고: 코어 우회 사례

- `app/config/config.py` 는 `config.toml` 경로를 기본으로 레포 루트에 두고
  import 시점에 로드한다. 설정을 영속 스토리지에 두기 위해
  `app/promo/configmap.py` 의 `ensure_config()` 가 앱 기동 전에
  `<storage>/config.toml` 을 시드(+SaaS 차단값 주입)하고, 코어는 env
  `MPT_CONFIG_FILE`(위 코어 수정 예외)로 그 경로를 직접 읽고 쓴다.
  과거의 루트 `config.toml` symlink 연결 방식은 제거되었다.

## 외부 코드/도구 채택 결정 (2026-07-24)

- **레포 공개**: `nimej99/jemshorts` 는 PUBLIC 이다 (오너 결정).
- **OpenMontage (AGPL-3.0) 코드 차용 허용**: 오너가 소스 공개를 전제로 승인.
  AGPL 코드를 처음 들여오는 커밋에서 **루트 LICENSE 를 AGPL-3.0 으로 교체**하고
  (MIT upstream 고지는 유지 — MIT→AGPL 결합은 적법), 차용 파일마다 출처
  (OpenMontage 경로 + 커밋 해시)를 파일 헤더와 이 문서에 등재한다.
  아직 차용된 코드는 없다 — 현재 LICENSE 는 MIT 그대로.
- **agent-reach (MIT)**: 제품 임베드 아님. 개발/리서치 도구로 로컬 설치
  (`uv tool install git+https://github.com/Panniantong/agent-reach`).
  용도: 경쟁 쇼츠 자막 추출 (`yt-dlp --skip-download --write-auto-subs
  --sub-langs "ko,en" --js-runtimes node <URL>`), 레퍼런스 리서치.
- **agency-agents (MIT)**: 개별 페르소나 파일만 참고용으로
  `~/.claude/agents/` 에 설치 (video-streaming-engineer, prompt-engineer).

## 외부 코드/도구 채택 결정 2차 (2026-07-25)

- **Postiz (AGPL-3.0) 채택**: 셀프호스트 소셜 스케줄링. 별도 서비스로
  띄우고 공개 API 만 HTTP 호출 (`app/promo/publish.py`) — 코드 결합이
  없어 LICENSE 영향 없음. upload-post.com(유료) 대체 경로.
  셀프호스트 실기동 검증은 운영 셋업 시점에 수행 (어댑터는 공식 API 문서
  docs.postiz.com/public-api 기준 구현 + 모킹 테스트 완료).
- **Postiz 실기동 검증 완료 (2026-08-22)**: `docker-compose.postiz.yml`
  (postiz + postgres17 + redis7 + temporal 스택, 공식 gitroomhq/postiz-app
  기준) 로 로컬 기동 확인. 공개 API 베이스는 `/api/public/v1` (프론트 `/public/v1`
  아님 — 307 로 `/auth` 리다이렉트됨). 인증은 `Authorization: <publicApi 키>`
  (헤더에 키 원문만, `Bearer` 불필요). 키는 웹 가입 후 `/api/user/self`
  응답의 `publicApi` 필드. 이미지 `ghcr.io/postiz/postiz` 는 존재하지 않으며
  올바른 이미지는 `ghcr.io/gitroomhq/postiz-app:latest`. 시크릿은 `docker-
  compose.postiz.env`(gitignore) 로 주입. `dynamicconfig/development-sql.yaml`
  은 temporal 설정용 번들 리소스.
- **Postiz 게시 계약 실측 검증 (2026-08-25)**: 현재 버전 DTO 기준 —
  (1) `settings.selfDeclaredMadeForKids` 는 불리언이 아니라 `"yes"|"no"`.
  (2) `settings.tags` 는 문자열 배열이 아니라 `{value, label}` 객체 배열.
  (3) 미디어 경로는 자체 URL 이 아닌 컨테이너 로컬 경로 전달: SSRF 안전
  디스패처가 자체 `localhost` URL fetch 를 차단(`Blocked IP`)하므로, `path` 에서
  오리진+`/uploads` 접두를 제거한 `/2026-08-24/...` 를 넘긴다 — posts.service 가
  비-HTTP 경로 앞에 `UPLOAD_DIRECTORY(/uploads)` 를 재결합해 최종 로컬 경로가 됨.
  이 경로로 젬쇼츠 채널 첫 쇼츠 게시 성공 (videoId kDd_ZD69jBg).
- **커머스 소재/댓글 안전 게이트 (2026-08-25)**: 상품 페이지 전체 `img`
  수집은 추천상품(햄/채소 등)이 섞이므로 금지. 상품 선별 결과의 대표 이미지를
  신뢰 기준으로 삼고 색상 히스토그램 유사도 0.75 이상인 후보만 렌더에 사용한다.
  전 후보가 탈락하면 대표 이미지만 크롭/줌으로 재사용해 타 상품 노출보다
  안전하게 수렴한다. Postiz 비동기 게시 후 YouTube uploads playlist 를 저비용
  API로 폴링해 쿠팡 파트너스 구매 링크+경제적 이해관계 댓글을 자동 등록한다.
  Shorts 설명/댓글의 일반 URL은 YouTube 정책상 클릭 불가이며, 클릭 가능한
  쇼핑 스티커는 YPP/YouTube Shopping 자격과 Studio 상품 태깅이 별도로 필요하다.

## 커머스 자동화 실행 계약 (에이전트/하위 모델 공통)

다른 모델도 아래 상태 순서와 완료 조건을 그대로 사용한다. 일부 단계만 성공한
상태를 “자동화 완료”라고 보고하지 않는다.

1. `RESEARCHED`: 네이버 검색 트렌드/쇼핑 수요와 영상 공급 갭으로 후보 선정.
2. `PRODUCT_LOCKED`: 쿠팡 상품의 `productId`, `itemId`, `vendorItemId`, 실제
   상품명·가격·평점·리뷰 수를 한 레코드로 고정.
3. `LINK_READY`: 위 상품 식별자에서 만든 `link.coupang.com` 파트너스 링크
   확인. 일반 쿠팡 URL이나 다른 옵션 링크로 대체 금지.
4. `ASSETS_VALIDATED`: 상품 선별 결과의 대표 이미지를 기준 이미지로 사용.
   상품 페이지 전체 `img` 수집 금지. 상세 후보는
   `app.promo.materials.product_images.validate_product_images` 통과분만 사용.
5. `RENDER_QA`: 렌더 후 훅/본문/CTA 프레임을 각각 추출해 상품·가격·고지
   일치 여부 확인. 잘못된 상품 이미지 하나라도 있으면 게시 금지.
6. `PUBLISHED`: Postiz 공개 API로 게시하고 YouTube 실제 videoId/채널/제목을
   oEmbed 또는 Data API로 검증.
7. `HUB_UPDATED`: 공개 제품 허브에 상품 식별자·검증 이미지·가격·파트너스
   링크를 등록하고 배포 URL에서 실제 버튼 목적지를 확인.
8. `COMMENTED`: 제품 허브 링크와 쿠팡 파트너스 경제적 이해관계 문구를 댓글로 등록.
   Shorts 일반 URL은 클릭 불가임을 전제로 설명란에도 같은 링크를 유지.

- **Shorts 자막 안전영역**: 프로모션 렌더는 `subtitle_position=custom`,
  `custom_position=62.0` 고정. 렌더 파일의 하단이 비어 보여도 실제 Shorts
  앱의 제목·채널·음원 UI가 하단 자막을 덮으므로 `bottom` 배치 금지.

### 링크 허브 원칙

- 채널 프로필에는 제품별 링크를 계속 교체하지 않고 **공개 제품 허브 URL
  하나**를 둔다.
- 허브의 각 버튼은 리다이렉트로 제휴 주소를 숨기지 말고 해당 쿠팡 파트너스
  URL로 직접 연결하며 `[광고]`와 수수료 고지를 제품 목록 상단에 표시한다.
- LinkStack은 오픈소스 링크인바이오로 쓸 수 있지만 공식 자동화 API가 없어,
  이 파이프라인에는 상품 레코드에서 정적 허브를 생성·배포하는 방식이 우선이다.

### 생성 이미지 원칙

- 생성 모델에 상품 패키지·로고·기능을 다시 그리게 하지 않는다(표기·색상·구성
  환각 방지).
- AI는 자동차 실내·냄새 문제·청량한 배경 같은 **라이프스타일 배경만 생성**.
  검증된 실제 상품 대표 이미지를 전경에 합성한다.
- 생성 배경도 렌더 QA 대상이며 실제 상품 효능을 암시하는 과장 표현은 금지.

### 커머스 성과 원장·일일 갱신

- SQLite `commerce_products`가 상품 식별자/영상 매핑의 단일 원장,
  `commerce_snapshots`가 `youtube|coupang|market|naver` 원천별 시계열이다.
- `commerce_offers`는 같은 상품의 쿠팡·네이버 쇼핑 커넥트 등 판매처별
  가격·배송·제휴 URL·수수료율을 보관한다. 허브는 활성 오퍼를 소비자가
  실제 지불할 가격 오름차순으로 전부 표시하고 최저가를 명시한다.
- 운영 순위는 소비자에게 보이는 최저가와 별개다. 시청자에게 특정 판매처를
  숨기거나 수수료가 높은 링크만 노출하지 않고, 내부에서는 판매처별 실제
  EPC·전환율·수수료를 비교한다.
- `scripts/commerce_daily.py`가 YouTube 조회·반응, 네이버 수요, 허브 가격
  스냅샷을 수집하고 1·3·7일 수수료/EPC/전환율 랭킹을 계산한다.
- 쿠팡 공식 API 승인 전 클릭·구매·수수료는 로그인 브라우저 리포트를
  JSON으로 추출해 `--coupang-json`으로 적재한다. 임의 추정값 저장 금지.
- macOS LaunchAgent `com.jemshorts.commerce-daily`가 매일 08:30 실행하며
  로컬 결과는 `storage/commerce-metrics/YYYY-MM-DD.json`에 보관한다.

### 검색 유입·비교 영상

- `scripts/build_product_hub.py`가 상품별 canonical/OG/Product JSON-LD 페이지,
  `sitemap.xml`, `robots.txt`를 생성한다. GitHub Pages 배포 전에 항상 실행.
- **쿠팡 쇼츠 기본 단위는 일일 TOP3**:
  `scripts/commerce_roundup.py` (`--top` 기본값 3). 수요 갭이 큰 한 주제를
  고른 뒤 그 주제에서 상품 3개를 잠그고, 각 상품의 식별자·대표 이미지·링크를
  독립 검증한 후 3위→1위 순으로 소개한다.
- **TOP5는 주간 결산**: `scripts/commerce_roundup.py --top 5`. 7일 실제
  수수료/EPC 우선 순위의 활성 상품만 사용한다.
- 단일 상품 `scripts/commerce_pick.py`는 가격 급락·신상품·TOP3 우승 상품의
  심화 리뷰에만 사용한다. 무조건 단일 상품 1개씩 게시하는 운영은 금지한다.

### 네이버 블로그·클립 재가공

- `scripts/commerce_repurpose.py`가 동일 원장에서 네이버 블로그용 비교 글과
  상품별 클립 캡션·원본 MP4 묶음을 생성한다.
- 블로그는 영상 대본 복붙이 아니라 비교표, 판매처별 링크, 구매 기준, FAQ,
  가격 확인일을 포함한다. 광고/제휴 고지는 글 첫 부분에 둔다.
- 클립은 상품별 문제·가격·허브 CTA 문법을 사용한다. 네이버 쇼핑 커넥트
  가입 후 `commerce_offers`에 네이버 오퍼를 추가해 상품 태그/링크를 연결한다.
- 네이버 로그인·최초 브랜드 커넥트 약관 동의는 사용자 조작이 필요하며,
  가입 전에는 생성 패키지만 보관하고 쇼핑 커넥트 링크를 꾸며내지 않는다.
- **Scrapling (BSD-3) 조건부 채택**: 네이버 공식 지역검색 API 로 부족할
  때의 보조 수집 카드. enrich 계층(PR #1)에 의존하므로 **PR #1 머지 후**
  config 옵션 플래그로 통합한다. 봇차단 우회는 약관 리스크 상존 — 기본
  비활성.
- **last30days-skill (MIT)**: dev-time 리서치 스킬. `~/.claude/skills/
  last30days` 에 설치 완료. 니치/경쟁 조사용, 제품 임베드 아님.
- **reelforge (Apache-2.0) 참고 채택**: Korean-first 동일 도메인. 한국어
  자막/폰트(Pretendard, D2Coding OFL) 처리 벤치마크 대상. 코드 차용 시
  Apache 고지 유지.
- **vox-director / Orkas-VideoStudio (MIT) 설계 차용**: beats/timeline
  스펙 분석 → 템플릿 v2 설계 (`docs/TEMPLATE_V2_DESIGN.md`). 코드 차용
  아님 (필요 시 MIT 라 가능).
