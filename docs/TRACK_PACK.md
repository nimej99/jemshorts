# TRACK_PACK — promo-shorts 기본 BGM 트랙팩

MPT(MoneyPrinterTurbo) 업스트림이 번들하던 `resource/songs/output0xx.mp3` 29개는
출처 불명의 유튜브 추출 음원으로 상용 서비스에 부적합하여 전량 삭제하고,
라이선스가 명확한 무료 음원으로 교체했다.

## 허용 기준

1. **CC0 (Public Domain) 우선.** 저작자표시 의무가 없어 자동 생성 파이프라인에서
   가장 안전하다. 현재 트랙팩 9개 전부 CC0 (FreePD.com 배포분).
2. **CC-BY 는 조건부 허용.** 부족분을 채울 때만 사용하며(우선 후보: Kevin MacLeod
   incompetech.com, CC-BY 4.0), 사용 시 앱이 영상 크레딧·캡션에 저작자표시 문자열을
   자동 포함하도록 처리해야 한다. 아래 표의 "저작자표시 문자열" 컬럼이 그 원문이다.
3. **NC / ND / SA 변형은 제외.** 상용 이용(NC), 편집·컷 사용(ND), 산출물 라이선스
   전염(SA) 각각과 충돌하므로 어떤 경우에도 넣지 않는다.

## 파일명 규칙

`<mood>-<slug>.mp3` — mood 는 `upbeat` / `calm` / `energetic` 3종, slug 는 원곡명의
소문자-하이픈 표기.

## 트랙 목록

원 출처 FreePD.com 은 2026년 초 서비스를 종료했다. 라이선스 근거와 재다운로드
경로는 Internet Archive Wayback Machine 스냅샷을 사용한다(아카이브 URL 이 실제
다운로드 경로이며 `scripts/fetch_trackpack.py` 가 자동 변환한다). FreePD 는 전 트랙을
"Creative Commons 0 — Free for Commercial Use, Free Of Royalties, Free Of
Attribution" 조건으로 배포했다(각 카테고리 페이지 푸터 및 트랙별 "available for
commercial and non-commercial purposes" 문구, 스냅샷에서 확인 가능).

| 파일명 | mood | 출처 URL (원본 → 아카이브) | 라이선스 | 저작자표시 문자열 | 검증일 |
|---|---|---|---|---|---|
| upbeat-city-sunshine.mp3 | upbeat | https://freepd.com/music/City%20Sunshine.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/City%20Sunshine.mp3 | CC0 | 불필요 (참고: "City Sunshine" by Kevin MacLeod, freepd.com) | 2026-07-19 |
| upbeat-happy-whistling-ukulele.mp3 | upbeat | https://freepd.com/music/Happy%20Whistling%20Ukulele.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Happy%20Whistling%20Ukulele.mp3 | CC0 | 불필요 (참고: "Happy Whistling Ukulele" by Rafael Krux, freepd.com) | 2026-07-19 |
| upbeat-advertime.mp3 | upbeat | https://freepd.com/music/Advertime.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Advertime.mp3 | CC0 | 불필요 (참고: "Advertime" by Rafael Krux, freepd.com) | 2026-07-19 |
| calm-lovely-piano-song.mp3 | calm | https://freepd.com/music/Lovely%20Piano%20Song.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Lovely%20Piano%20Song.mp3 | CC0 | 불필요 (참고: "Lovely Piano Song" by Rafael Krux, freepd.com) | 2026-07-19 |
| calm-nostalgic-piano.mp3 | calm | https://freepd.com/music/Nostalgic%20Piano.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Nostalgic%20Piano.mp3 | CC0 | 불필요 (참고: "Nostalgic Piano" by Rafael Krux, freepd.com) | 2026-07-19 |
| calm-landras-dream.mp3 | calm | https://freepd.com/music/Landra's%20Dream.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Landra%27s%20Dream.mp3 | CC0 | 불필요 (참고: "Landra's Dream" by Jason Shaw, freepd.com) | 2026-07-19 |
| energetic-arpent.mp3 | energetic | https://freepd.com/music/Arpent.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Arpent.mp3 | CC0 | 불필요 (참고: "Arpent" by Kevin MacLeod, freepd.com) | 2026-07-19 |
| energetic-beat-one.mp3 | energetic | https://freepd.com/music/Beat%20One.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Beat%20One.mp3 | CC0 | 불필요 (참고: "Beat One" by Kevin MacLeod, freepd.com) | 2026-07-19 |
| energetic-goodnightmare.mp3 | energetic | https://freepd.com/music/Goodnightmare.mp3 → https://web.archive.org/web/20250107id_/https://freepd.com/music/Goodnightmare.mp3 | CC0 | 불필요 (참고: "Goodnightmare" by Kevin MacLeod, freepd.com) | 2026-07-19 |

- mood 분류 근거: FreePD 카테고리(upbeat = Upbeat/Positive, calm = Romantic/Sentimental
  중 잔잔한 곡, energetic = Electronic 중 드라이빙 비트 곡).
- 2026-07-19 기준 9/9 트랙 확보 완료(목표 mood별 3개 충족). 전 트랙 ffprobe 로
  mp3 오디오 스트림 정상 확인. 일부 파일(calm-landras-dream, energetic-beat-one)에
  임베디드 커버아트 스트림이 포함되어 있으나 재생·믹싱에는 영향 없다.

## 재현 방법

```bash
python3 scripts/fetch_trackpack.py          # 없는 파일만 다운로드
python3 scripts/fetch_trackpack.py --force  # 전체 재다운로드
```

트랙 추가·교체 시 이 표와 `scripts/fetch_trackpack.py` 의 `TRACKS` 목록을 함께
갱신할 것 (스크립트 목록과 표는 1:1 로 유지).

## CC-BY 사용 시 처리 (현재 미사용)

CC-BY 트랙을 추가하는 경우:

1. 표의 "저작자표시 문자열" 컬럼에 크레딧 원문을 기입한다.
   예: `"Track Name" Kevin MacLeod (incompetech.com) Licensed under Creative Commons: By Attribution 4.0 License http://creativecommons.org/licenses/by/4.0/`
2. 앱 렌더 파이프라인이 해당 문자열을 영상 크레딧(설명란 텍스트) 및 캡션에
   자동 포함하도록 연동한 뒤에만 트랙팩에 편입한다.
