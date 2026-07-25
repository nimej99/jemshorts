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

`structural_gate` v2 — failures 에 기계 판독 코드 도입:

| 코드 | 판정 | 허용오차 |
| --- | --- | --- |
| `SECTION_GAP` | 섹션 경계 사이 공백 | +0.05s |
| `SECTION_OVERLAP` | 섹션 겹침 | −0.001s |
| `TIMELINE_COVERAGE_MISMATCH` | 섹션 합계 vs 실측 오디오 길이 | ±`timing.tolerance_s` |
| `HEADLINE_MISSING_ON_HOOK` | hook 에 headline.show=true 인데 텍스트 없음 | — |

(섹션 경계 실측값은 TTS 문장별 길이에서 나온다 — 구현 (b) 이후 활성.)

## 6. 구현 순서 (각각 독립 PR 크기)

- (a) 스키마 v2 optional 필드 + 로더/검증 확장 — 코어 무접촉
- (b) 내레이션 실측 타이밍: 스크립트를 섹션별 문장으로 분할 → 문장별
  TTS 길이 실측 → 섹션 경계 재계산 → 코어 클립 길이 입력에 반영
- (c) 컷인 shots: 동일 소재 crop/zoom 파생 클립 생성 (ffmpeg crop+scale)
- (d) headline 오버레이: moviepy TextClip 레이어 (코어 자막과 독립)
- (e) style_preset → 소재 생성 프롬프트 (ComfyUI/외부 API 연동 시)

## 7. 오픈소스 채택 결정과의 관계

- vox-director / Orkas 는 **스펙 참고**(MIT — 필요 시 코드 차용도 가능하나
  현재는 설계 차용만). 근거 파일은 본 문서 서두에 고정.
- postiz(AGPL) 는 별도 서비스 HTTP 호출로 채택 — `app/promo/publish.py`.
- Scrapling(BSD-3) 은 네이버 공식 API 부족 시의 보조 카드 — enrich 계층
  (PR #1) 머지 후 옵션 플래그로 통합 예정.
