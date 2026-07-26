# 템플릿 v2 설계 — vox-director beats / Orkas CompositionManifest 정밀 분석

vox-director(MIT)와 Orkas-VideoStudio(MIT)의 실물 스펙을 해부해 템플릿 v2
확장안을 도출한 기록. 분석 근거는 각 레포의 실제 파일이다:
- vox-director `examples/money-60s-9x16-english.beats.json` (완성 산출물 포함)
- vox-director `examples/cr7-act.elements_spec.json`
- Orkas `packages/core/src/composition/manifest.ts` (검증기 전문)

## 1. 현행 v1 요약과 실측된 한계

v1 (`app/promo/templates/schema.py`):
`role(hook/body/cta) + duration_s + script_guide + material_slot` 평면 섹션.

M1/M2 실렌더에서 확인된 한계:
1. **내레이션-비주얼 동기화 부재** — 오디오 19.25초 vs 소재 12초일 때 MPT
   코어가 클립을 루프시켜 채움 (M1 실측). 섹션 경계와 내레이션 문장
   경계가 어긋난다.
2. **섹션당 소재 1개** — 같은 소재가 duration 내내 지속. 컷 변화 없음.
3. **텍스트 오버레이 스펙 없음** — 자막(코어)만 있고 헤드라인 배너 없음.
4. **무드가 전역 1개** — BGM `random` 선택, 섹션별 감정 변화 표현 불가.
5. **스타일/모션 지시 없음** — 소재 생성(ComfyUI 등)을 붙일 때 프롬프트를
   만들 자리가 스키마에 없다.

## 2. vox-director beats 스펙 해부

구조 (60초 실물 기준, 6 beats × 2 shots):

```text
전역:  project/topic/language/aspect/style/video_model
       voice{gender,tone,voice_id,language,speed}
       music: "cinematic documentary score ..." (생성 프롬프트 문자열)
beat:  id, title_en(헤드라인), bg(팔레트), feel(감정),
       narration(비트당 1~2문장),
       shots[]: {id a|b, dur, title(bool), scene, motion,
                 keyframe_prompt, keyframe_url/path, clip_url/path}
       narration_audio + narration_dur(실측 초)
```

가져갈 인사이트 4개:

1. **내레이션이 타임라인의 주인이다.** `narration_dur` 를 TTS 후 실측해서
   비트 길이를 확정한다. 우리 한계 1번의 정답 — 템플릿의 `duration_s` 는
   "목표값"이고, 렌더 시점에 문장별 TTS 실측으로 섹션 경계를 재계산해야
   한다.
2. **beat = 와이드샷(a) + 컷인(b) 페어.** 같은 씬의 디테일 컷인으로 시각
   변화를 만든다. 소상공인 소재(사진 몇 장)에도 그대로 적용 가능: 같은
   사진의 풀샷 + 중앙 크롭 줌인 2컷.
3. **스타일 상수와 씬 변수의 분리.** `keyframe_prompt` = 고정 스타일
   프리픽스(콜라주 스타일 문단, 모든 샷 동일) + `SCENE:` 슬롯(샷별 가변).
   우리 `build_script_prompt` 와 동형 구조 — 템플릿 `style_preset` 필드로
   승격하면 소재 생성 프롬프트가 스키마에서 나온다.
4. **헤드라인은 첫 샷에만** (`title: true/false`). 컷인에는 배너를 빼서
   시각 밀도를 조절한다.

`elements_spec.json` (bbox/cutout/erase 로 카드 이미지에서 요소 분리 →
패럴랙스): 소재 생성/후처리 단계의 스펙이라 **v2 범위에서 제외** (과설계).

## 3. Orkas CompositionManifest 해부

구조 (manifest.ts 검증기 기준):

```text
composition: {id, width, height, duration(≤600), fps(≤60), language}
scenes[]:    {id, start, duration, approved_copy[], narration_refs[],
              narration_text?, source_shots[], roles[]}
audio:       {owner: composition|assembler|none,
              tracks[]: {kind: narration|music|sfx, src(상대경로 강제),
                         start, duration, volume 0~1},
              narration_intent{voice_ref, language, speed 0.5~2}}
```

가져갈 인사이트 3개:

1. **타임라인 정합성 검증기의 오류 코드 체계.** SCENE_GAP(+0.05s 초과 공백)
   / SCENE_OVERLAP(−0.001s) / SCENE_OUT_OF_RANGE / TIMELINE_COVERAGE_MISMATCH
   (전체 ±0.15s) — 허용오차까지 명시된 결정적 검증. 우리 `structural_gate`
   의 v2 확장 방향과 정확히 일치한다. **오류 코드 문자열 체계**(사람이
   읽는 메시지와 분리된 기계 판독 코드)도 게이트 failures 에 도입할 가치.
2. **audio ownership 명시.** "오디오를 누가 붙이는가"를 스키마에 선언
   (composition|assembler|none) → 소유권 충돌을 검증기가 잡는다. 우리는
   MPT 코어가 오디오 주인 — v2 에 `timing.owner` 로 이 사실을 명시해두면
   나중에 렌더러를 교체할 때 계약이 깨지지 않는다.
3. **approved_copy 가 씬에 귀속.** 승인된 카피 문자열이 타임라인 객체에
   붙는다 — 우리 plan 승인 게이트(승인한 것 = 렌더되는 것 원칙)와 같은
   철학. v2 에서 섹션별 스크립트 분할이 생기면 같은 방식으로 귀속시킨다.

채택하지 않는 것: audio tracks 배열(멀티트랙 믹싱 — MPT 코어가 담당),
fps/width 커스텀(코어 고정 1080x1920), narration_refs 간접 참조(우리는
스크립트 원문 직접 귀속으로 충분).

## 4. v2 스키마 제안 (v1 완전 하위호환)

```jsonc
{
  "template_id": "upbeat-new-menu-v2",
  "version": 2,
  "mood": "upbeat",
  "style_preset": "clean-product",        // 신규(선택): 소재 생성 프롬프트 프리셋 키
  "voice": { "speed": 1.0 },              // 신규(선택): 템플릿별 낭독 속도
  "timing": {                              // 신규(선택): 타임라인 소유 선언
    "owner": "narration",                  // narration: TTS 실측이 섹션 경계 결정
    "tolerance_s": 0.15                    // Orkas 커버리지 허용오차 채택
  },
  "structure": [
    {
      "role": "hook",
      "duration_s": 3,                     // v1 유지 — owner=narration 이면 목표값
      "script_guide": "…",
      "material_slot": "video",
      "headline": {                        // 신규(선택): vox title 배너
        "template": "{menu_name} 출시!",
        "show": true                       // 컷인에는 false
      },
      "shots": [                           // 신규(선택): 와이드+컷인 페어
        { "kind": "wide",  "motion": "push-in" },
        { "kind": "cutin", "motion": "drift", "crop": "center-zoom" }
      ],
      "feel": "따뜻한, 식욕을 돋우는"        // 신규(선택): 생성/보정 힌트
    }
  ],
  "total_duration_range": [15, 30],
  "caption_template": "…",
  "hashtags_base": ["…"]
}
```

호환 규칙:
- v2 필드는 **전부 optional** — `version: 1` 파일은 무수정으로 계속 검증
  통과한다 (`validate_template` 은 version 값에 따라 추가 필드만 해석).
- `shots` 미지정 = 현행 동작(섹션당 1소재). `timing` 미지정 = 현행 동작
  (duration_s 고정 + 코어 루프).

## 5. 게이트 확장 (Orkas 검증기 차용)

**구현 완료** — 모든 게이트 failures/warnings 가 `CODE: 사유` 형식이 됐다
(`app/promo/quality/gates.py`). 코드는 기계 판독용, 뒤 문장은 사용자 노출용.

| 코드 | 게이트 | 판정 | 허용오차 |
| --- | --- | --- | --- |
| `TIMELINE_COVERAGE_MISMATCH` | timeline | 실측 내레이션 총 길이 vs `total_duration_range` | ±`timing.tolerance_s` |
| `SECTION_DURATION_DRIFT` | timeline (warning) | 섹션 목표 대비 실측 편차 | max(tolerance, 목표×25%) |
| `SECTION_ROLE_MISSING` / `MATERIALS_EMPTY` / `BRAND_MATERIAL_MISSING` | structural | 기존 판정에 코드 부여 | — |
| `BRAND_MATERIAL_DOWNGRADED` | structural (warning) | 브랜드 소재 0개 강등 사유 | — |
| `FILE_MISSING` / `PROBE_FAILED` / `VIDEO_STREAM_MISSING` / `AUDIO_STREAM_MISSING` / `RESOLUTION_MISMATCH` / `DURATION_UNREADABLE` / `DURATION_OUT_OF_RANGE` | technical | 기존 판정에 코드 부여 | — |

설계안에 있던 `SECTION_GAP` / `SECTION_OVERLAP` 은 **채택하지 않는다**:
섹션 경계를 0 부터 실측 누적으로 계산하므로(`app/promo/timing.py`) 공백과
겹침이 구조적으로 발생할 수 없다. 없는 실패를 검사하는 코드는 죽은 코드다.
`HEADLINE_MISSING_ON_HOOK` 도 불필요 — 스키마가 `headline.template` 을
비어있지 않은 문자열로 강제하므로 게이트까지 갈 수 없다.

## 6. 구현 순서 (각각 독립 PR 크기)

- **(a) 완료** — 스키마 v2 optional 필드 + 로더/검증 확장 (코어 무접촉).
  `app/promo/templates/schema.py`:
  - `Shot / Headline / VoiceSpec / TimingSpec` 추가, `Section.headline|shots|feel`,
    `Template.style_preset|voice|timing` 전부 optional.
  - `MAX_TEMPLATE_VERSION = 2` — 미지원 상위 버전은 거부(조용한 무시 금지).
  - **version 1 문서에 v2 필드가 있으면 거부** — 무시하면 "선언한 것 ≠ 렌더된
    것"이 되어 승인 게이트 전제가 깨진다.
  - 샷/모션/크롭/타이밍 owner 는 닫힌 어휘 + 오브젝트 미지 키 거부(오타 차단),
    `shots[0].kind == "wide"` 강제(컷인은 와이드 파생).
  - 즉시 소비되는 두 필드: `voice.speed` → `VideoParams.voice_rate`
    (`RenderPlan.voice_rate` 파생 프로퍼티 — 영속화 없이 템플릿 스냅샷에서 재계산),
    `section.feel` / `section.headline` → `build_script_prompt` 힌트, 그리고
    `char_budget` 이 낭독 속도만큼 글자수 예산을 비례 조정.
  - 번들 시드 3종은 v1 유지 — 렌더 지원이 붙기 전까지 로테이션 동작 불변.
- **(b-1) 완료** — 내레이션 실측 타이밍의 앞단 (`app/promo/timing.py`).
  - 스크립트를 문장으로 분리 → 섹션 목표 길이 비율대로 배분(섹션당 최소
    1문장, 원문 순서 보존) → 섹션별 낭독 길이 **실측** → 0 부터 누적으로
    섹션 경계 확정.
  - 실측 함수는 주입식(`MeasureFn`). 기본 구현 `tts_measurer` 는 코어 TTS 를
    지연 임포트해 쓰고, 측정 오디오는 임시 디렉터리에 쓴 뒤 즉시 지운다.
  - `plan_render` 는 템플릿이 `timing.owner = "narration"` 을 선언한 경우에만
    실측한다. 결과는 `RenderPlan.narration` + `timeline_gate` 판정
    (`RenderPlan.timeline`)으로 승인 게이트에 제시되고, 실패면
    `approved_ready = False` — **렌더 비용을 쓰기 전에** 길이 불일치를 잡는다.
  - 실측값과 게이트 판정은 payload 에 영속화한다 (재측정 편차로 승인 결과가
    뒤집히면 안 된다). 구버전 payload 는 키 없이도 복원된다.
- **(b-2) 완료** — 실측 경계를 화면에 반영 (`app/promo/materials/retime.py`).
  - 섹션 소재를 실측 길이 클립으로 다시 만든다: 사진은 그 길이만큼 정지
    클립으로 렌더, 영상은 길면 자르고 짧으면 루프해서 채운다(무음 `-an`).
  - `RenderPlan.clip_duration_s` = 가장 긴 섹션 + 꼬리 여유 올림 →
    `VideoParams.video_clip_duration`. 소재가 이미 섹션 길이이고 이 값이
    그보다 크거나 같으면 코어가 클립을 더 쪼개지 않는다.
  - **코어 실측 검증** (색상 구분 클립 3개 + 15초 무음 오디오로
    `combine_videos` 직접 호출, 프레임 YUV 샘플링):
    hook(0~3s) red / body(3~11s) green / cta(11~15s) blue — 화면 전환이
    섹션 경계와 정확히 일치.
  - 같은 검증에서 **아티팩트를 하나 잡았다**: 코어는 `오디오 + 0.1초`
    (`_VIDEO_DURATION_SAFETY_MARGIN`) 만큼의 영상을 요구하고 모자라면 앞
    클립부터 재사용한다 → 끝에서 훅 화면이 66ms 번쩍였다. 마지막 섹션
    클립에만 꼬리 여유(기본 1.0초)를 붙여 제거했고(재검증: `concatenating
    3 clips`, 루프 경고 없음), 코어 상수가 바뀌면 깨지는 가드 테스트를 뒀다.
  - 리타이밍은 플랜 시점에 수행한다 — 승인 화면에 제시된 소재 = 실제
    렌더되는 소재. 소재 길이는 payload 에 함께 저장한다(구버전
    `material_urls` payload 도 계속 복원).
- (b-3) 오디오 재생성 회피: 코어 `start(..., voice_preview={script, voice_name,
  voice_rate, voice_volume, audio_file, duration, sub_maker})` 재사용 경로 —
  `audio_file` 이 `utils.task_dir(task_id)` 안이고 `voice_volume == 1.0` 일 때만
  채택된다 (`app/services/task.py:_resolve_reusable_voice_preview`). 이 경로면
  sub_maker 가 함께 전달돼 자막도 그대로 생성된다. 현재는 실측 TTS 와 렌더
  TTS 가 각각 호출된다(edge-tts 무료 경로 기준 허용).
- (c) 컷인 shots: 동일 소재 crop/zoom 파생 클립 생성 (ffmpeg crop+scale)
- (d) headline 오버레이: moviepy TextClip 레이어 (코어 자막과 독립)
- (e) style_preset → 소재 생성 프롬프트 (ComfyUI/외부 API 연동 시)

## 7. 오픈소스 채택 결정과의 관계

- vox-director / Orkas 는 **스펙 참고**(MIT — 필요 시 코드 차용도 가능하나
  현재는 설계 차용만). 근거 파일은 본 문서 서두에 고정.
- postiz(AGPL) 는 별도 서비스 HTTP 호출로 채택 — `app/promo/publish.py`.
- Scrapling(BSD-3) 은 네이버 공식 API 부족 시의 보조 카드 — enrich 계층에
  옵션 플래그(`promo_scrapling_enabled`, 기본 off)로 통합 완료 (PR #3).
