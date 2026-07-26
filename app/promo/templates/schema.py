"""트렌드 템플릿 JSON 스키마 정의 + 로더.

템플릿 한 개는 아래 필드를 가진 JSON 오브젝트다.

- template_id: 고유 식별자 (비어있지 않은 문자열)
- name: 한국어 표시 이름
- version: 1 | 2 (v2 선택 필드는 version >= 2 에서만 해석 — docs/TEMPLATE_V2_DESIGN.md)
- mood: "upbeat" | "calm" | "energetic" (BGM 트랙 팩 mood 3종과 동일 — docs/TRACK_PACK.md)
- structure: 순서 있는 섹션 배열. 각 섹션은
    role: "hook" | "body" | "cta"
    duration_s: 양수 (초)
    script_guide: 한국어 가이드 문구
    material_slot: "photo" | "video" | "any"
- total_duration_range: [최소초, 최대초]
- caption_template: 캡션 템플릿 문자열
- hashtags_base: 기본 해시태그 문자열 배열

v2 선택 필드 (전부 optional — 없으면 v1 동작 그대로, 렌더러 무접촉):
- style_preset: 소재 생성 프롬프트 프리셋 키 (templates.style_presets 의 닫힌 어휘)
- voice: {speed: 0.5~2.0}
- timing: {owner: "narration" | "template", tolerance_s: 0 초과 1.0 이하}
    narration = TTS 실측이 섹션 경계를 결정한다는 선언 (렌더러 반영은 후속 단계)
- 섹션.headline: {template: 문자열, show: bool} — 헤드라인 배너 (코어 자막과 별개)
- 섹션.shots: [{kind: "wide" | "cutin", motion?, crop?}] — 첫 컷은 wide, 최대 4컷
- 섹션.feel: 감정/톤 힌트 문자열

전역 가드레일 (위반 시 TemplateValidationError, 사유 포함):
- 섹션 duration_s 합산이 10~60초 범위
- hook 섹션 필수이며 첫 번째 섹션이어야 함
- cta 섹션 필수
- 섹션 개수 >= 2
- mood 는 유효값 3종 중 하나
- version 은 1~MAX_TEMPLATE_VERSION 정수 (미지원 상위 버전은 거부)
- version 1 문서에 v2 필드가 있으면 거부 (조용히 무시하면 렌더 결과가 어긋난다)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.promo.templates.style_presets import VALID_STYLE_PRESETS

VALID_MOODS = ("upbeat", "calm", "energetic")
VALID_ROLES = ("hook", "body", "cta")
VALID_MATERIAL_SLOTS = ("photo", "video", "any")

# 필수 섹션 역할 — 게이트(quality.gates.structural_gate)와 공유하는 단일 계약.
# body 는 선택이다: hook + cta 2섹션 템플릿도 스키마상 유효하다.
REQUIRED_ROLES = ("hook", "cta")

# 전역 가드레일: 숏폼 총 길이 허용 범위 (초)
MIN_TOTAL_DURATION_S = 10
MAX_TOTAL_DURATION_S = 60
MIN_SECTIONS = 2

# 지원하는 최상위 스키마 버전. 상위 버전은 해석할 수 없으므로 거부한다.
MAX_TEMPLATE_VERSION = 2

# --- v2 선택 필드 어휘 (docs/TEMPLATE_V2_DESIGN.md §4) ---
VALID_SHOT_KINDS = ("wide", "cutin")
VALID_SHOT_MOTIONS = (
    "static",
    "push-in",
    "pull-out",
    "drift",
    "pan-left",
    "pan-right",
)
VALID_SHOT_CROPS = ("center-zoom", "top", "bottom", "left", "right")
VALID_TIMING_OWNERS = ("narration", "template")

MAX_SHOTS_PER_SECTION = 4
MIN_VOICE_SPEED = 0.5
MAX_VOICE_SPEED = 2.0
DEFAULT_TIMING_TOLERANCE_S = 0.15
MAX_TIMING_TOLERANCE_S = 1.0

# version >= 2 에서만 허용되는 필드 목록
V2_TEMPLATE_FIELDS = ("style_preset", "voice", "timing")
V2_SECTION_FIELDS = ("headline", "shots", "feel")


class TemplateValidationError(ValueError):
    """템플릿 스키마/가드레일 위반. 메시지에 위반 사유를 포함한다."""


@dataclass(frozen=True)
class Shot:
    """섹션 안의 컷 하나 (v2). wide=와이드샷, cutin=같은 소재의 디테일 컷인."""

    kind: str
    motion: str | None = None
    crop: str | None = None


@dataclass(frozen=True)
class Headline:
    """섹션 헤드라인 배너 (v2). 코어 자막과 독립 레이어."""

    template: str
    show: bool = True


@dataclass(frozen=True)
class VoiceSpec:
    """템플릿별 낭독 지시 (v2)."""

    speed: float = 1.0


@dataclass(frozen=True)
class TimingSpec:
    """타임라인 소유 선언 (v2). narration = TTS 실측이 섹션 경계를 결정."""

    owner: str
    tolerance_s: float = DEFAULT_TIMING_TOLERANCE_S


@dataclass(frozen=True)
class Section:
    role: str
    duration_s: float
    script_guide: str
    material_slot: str

    # --- v2 선택 필드 (미지정이면 v1 동작) ---
    headline: Headline | None = None
    shots: tuple[Shot, ...] = ()
    feel: str | None = None


@dataclass(frozen=True)
class Template:
    template_id: str
    name: str
    version: int
    mood: str
    structure: tuple[Section, ...]
    total_duration_range: tuple[float, float]
    caption_template: str
    hashtags_base: tuple[str, ...]

    # --- v2 선택 필드 (미지정이면 v1 동작) ---
    style_preset: str | None = None
    voice: VoiceSpec | None = None
    timing: TimingSpec | None = None

    @property
    def total_duration_s(self) -> float:
        return sum(section.duration_s for section in self.structure)


def _err(source: str, reason: str) -> TemplateValidationError:
    return TemplateValidationError(f"{source}: {reason}")


def _require_str(data: dict, key: str, source: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _err(source, f"'{key}' 는 비어있지 않은 문자열이어야 합니다")
    return value


def _is_number(value: object) -> bool:
    # bool 은 int 의 하위 타입이므로 명시적으로 제외한다.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_object(
    raw: object, where: str, source: str, allowed: tuple[str, ...]
) -> dict:
    """오브젝트 여부 + 허용 키만 있는지 검사한다 (오타를 조용히 무시하지 않는다)."""
    if not isinstance(raw, dict):
        raise _err(source, f"{where} 는 오브젝트여야 합니다")
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise _err(
            source,
            f"{where} 에 알 수 없는 필드가 있습니다: {', '.join(unknown)} "
            f"(허용: {', '.join(allowed)})",
        )
    return raw


def _reject_v2_fields(
    raw: dict, keys: tuple[str, ...], version: int, prefix: str, source: str
) -> None:
    """version 1 문서의 v2 필드를 거부한다 (무시하면 렌더 결과가 선언과 어긋난다)."""
    if version >= 2:
        return
    present = [f"{prefix}{key}" for key in keys if key in raw]
    if present:
        raise _err(
            source,
            f"{', '.join(present)} 는 version 2 이상에서만 사용할 수 있습니다 "
            f"(현재 version: {version})",
        )


def _parse_headline(raw: object, where: str, source: str) -> Headline:
    data = _require_object(raw, where, source, ("template", "show"))
    template = data.get("template")
    if not isinstance(template, str) or not template.strip():
        raise _err(source, f"{where}.template 는 비어있지 않은 문자열이어야 합니다")
    show = data.get("show", True)
    if not isinstance(show, bool):
        raise _err(source, f"{where}.show 는 true/false 여야 합니다")
    return Headline(template=template, show=show)


def _parse_shots(raw: object, where: str, source: str) -> tuple[Shot, ...]:
    if not isinstance(raw, list) or not raw:
        raise _err(source, f"{where} 는 비어있지 않은 배열이어야 합니다")
    if len(raw) > MAX_SHOTS_PER_SECTION:
        raise _err(
            source,
            f"{where} 는 최대 {MAX_SHOTS_PER_SECTION}컷까지 허용합니다 "
            f"(현재: {len(raw)}컷)",
        )

    shots: list[Shot] = []
    for index, item in enumerate(raw):
        at = f"{where}[{index}]"
        data = _require_object(item, at, source, ("kind", "motion", "crop"))
        kind = data.get("kind")
        if kind not in VALID_SHOT_KINDS:
            raise _err(
                source,
                f"{at}.kind '{kind}' 은 유효하지 않습니다 "
                f"(허용: {', '.join(VALID_SHOT_KINDS)})",
            )
        motion = data.get("motion")
        if motion is not None and motion not in VALID_SHOT_MOTIONS:
            raise _err(
                source,
                f"{at}.motion '{motion}' 은 유효하지 않습니다 "
                f"(허용: {', '.join(VALID_SHOT_MOTIONS)})",
            )
        crop = data.get("crop")
        if crop is not None and crop not in VALID_SHOT_CROPS:
            raise _err(
                source,
                f"{at}.crop '{crop}' 은 유효하지 않습니다 "
                f"(허용: {', '.join(VALID_SHOT_CROPS)})",
            )
        shots.append(Shot(kind=kind, motion=motion, crop=crop))

    # 컷인은 와이드샷의 디테일 파생 — 맥락 없이 먼저 나올 수 없다.
    if shots[0].kind != "wide":
        raise _err(source, f"{where}[0].kind 는 'wide' 여야 합니다 (컷인은 와이드 다음)")
    return tuple(shots)


def _parse_voice(raw: object, source: str) -> VoiceSpec:
    data = _require_object(raw, "'voice'", source, ("speed",))
    speed = data.get("speed", 1.0)
    if not _is_number(speed) or not (MIN_VOICE_SPEED <= speed <= MAX_VOICE_SPEED):
        raise _err(
            source,
            f"'voice.speed' 는 {MIN_VOICE_SPEED}~{MAX_VOICE_SPEED} 범위의 "
            f"숫자여야 합니다 (현재: {speed!r})",
        )
    return VoiceSpec(speed=float(speed))


def _parse_timing(raw: object, source: str) -> TimingSpec:
    data = _require_object(raw, "'timing'", source, ("owner", "tolerance_s"))
    owner = data.get("owner")
    if owner not in VALID_TIMING_OWNERS:
        raise _err(
            source,
            f"'timing.owner' '{owner}' 은 유효하지 않습니다 "
            f"(허용: {', '.join(VALID_TIMING_OWNERS)})",
        )
    tolerance_s = data.get("tolerance_s", DEFAULT_TIMING_TOLERANCE_S)
    if not _is_number(tolerance_s) or not (0 < tolerance_s <= MAX_TIMING_TOLERANCE_S):
        raise _err(
            source,
            f"'timing.tolerance_s' 는 0 초과 {MAX_TIMING_TOLERANCE_S} 이하의 "
            f"숫자여야 합니다 (현재: {tolerance_s!r})",
        )
    return TimingSpec(owner=owner, tolerance_s=float(tolerance_s))


def _parse_section(raw: object, index: int, source: str, version: int = 1) -> Section:
    where = f"structure[{index}]"
    if not isinstance(raw, dict):
        raise _err(source, f"{where} 는 오브젝트여야 합니다")

    role = raw.get("role")
    if role not in VALID_ROLES:
        raise _err(
            source,
            f"{where}.role '{role}' 은 유효하지 않습니다 (허용: {', '.join(VALID_ROLES)})",
        )

    duration_s = raw.get("duration_s")
    if not _is_number(duration_s) or duration_s <= 0:
        raise _err(source, f"{where}.duration_s 는 0보다 큰 숫자여야 합니다")

    script_guide = raw.get("script_guide")
    if not isinstance(script_guide, str) or not script_guide.strip():
        raise _err(source, f"{where}.script_guide 는 비어있지 않은 문자열이어야 합니다")

    material_slot = raw.get("material_slot")
    if material_slot not in VALID_MATERIAL_SLOTS:
        raise _err(
            source,
            f"{where}.material_slot '{material_slot}' 은 유효하지 않습니다 "
            f"(허용: {', '.join(VALID_MATERIAL_SLOTS)})",
        )

    _reject_v2_fields(raw, V2_SECTION_FIELDS, version, f"{where}.", source)

    headline = (
        _parse_headline(raw["headline"], f"{where}.headline", source)
        if "headline" in raw
        else None
    )
    shots = (
        _parse_shots(raw["shots"], f"{where}.shots", source) if "shots" in raw else ()
    )
    feel = raw.get("feel")
    if "feel" in raw and (not isinstance(feel, str) or not feel.strip()):
        raise _err(source, f"{where}.feel 는 비어있지 않은 문자열이어야 합니다")

    return Section(
        role=role,
        duration_s=float(duration_s),
        script_guide=script_guide,
        material_slot=material_slot,
        headline=headline,
        shots=shots,
        feel=feel,
    )


def validate_template(data: object, source: str = "<template>") -> Template:
    """템플릿 dict 를 검증하고 Template 로 변환한다. 위반 시 TemplateValidationError."""
    if not isinstance(data, dict):
        raise _err(source, "템플릿 루트는 JSON 오브젝트여야 합니다")

    template_id = _require_str(data, "template_id", source)
    name = _require_str(data, "name", source)
    caption_template = _require_str(data, "caption_template", source)

    version = data.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or not (1 <= version <= MAX_TEMPLATE_VERSION)
    ):
        raise _err(
            source,
            f"'version' 은 1~{MAX_TEMPLATE_VERSION} 범위의 정수여야 합니다 "
            f"(현재: {version!r})",
        )
    _reject_v2_fields(data, V2_TEMPLATE_FIELDS, version, "", source)

    style_preset = None
    if "style_preset" in data:
        style_preset = _require_str(data, "style_preset", source)
        if style_preset not in VALID_STYLE_PRESETS:
            raise _err(
                source,
                f"'style_preset' '{style_preset}' 은 유효하지 않습니다 "
                f"(허용: {', '.join(VALID_STYLE_PRESETS)})",
            )
    voice = _parse_voice(data["voice"], source) if "voice" in data else None
    timing = _parse_timing(data["timing"], source) if "timing" in data else None

    mood = data.get("mood")
    if mood not in VALID_MOODS:
        raise _err(
            source,
            f"'mood' '{mood}' 은 유효하지 않습니다 (허용: {', '.join(VALID_MOODS)})",
        )

    hashtags_raw = data.get("hashtags_base")
    if not isinstance(hashtags_raw, list) or not hashtags_raw:
        raise _err(source, "'hashtags_base' 는 비어있지 않은 배열이어야 합니다")
    for tag in hashtags_raw:
        if not isinstance(tag, str) or not tag.strip():
            raise _err(source, "'hashtags_base' 항목은 비어있지 않은 문자열이어야 합니다")

    structure_raw = data.get("structure")
    if not isinstance(structure_raw, list):
        raise _err(source, "'structure' 는 섹션 배열이어야 합니다")
    if len(structure_raw) < MIN_SECTIONS:
        raise _err(
            source,
            f"섹션은 최소 {MIN_SECTIONS}개 필요합니다 (현재: {len(structure_raw)}개)",
        )
    sections = tuple(
        _parse_section(raw, index, source, version)
        for index, raw in enumerate(structure_raw)
    )

    roles = [section.role for section in sections]
    for role in REQUIRED_ROLES:
        if role not in roles:
            raise _err(source, f"{role} 섹션은 필수입니다")
    if roles[0] != "hook":
        raise _err(source, "hook 섹션은 첫 번째에 위치해야 합니다")

    total = sum(section.duration_s for section in sections)
    if not (MIN_TOTAL_DURATION_S <= total <= MAX_TOTAL_DURATION_S):
        raise _err(
            source,
            f"총 길이 {total:g}초는 허용 범위({MIN_TOTAL_DURATION_S}~"
            f"{MAX_TOTAL_DURATION_S}초)를 벗어납니다",
        )

    range_raw = data.get("total_duration_range")
    if (
        not isinstance(range_raw, list)
        or len(range_raw) != 2
        or not all(_is_number(v) for v in range_raw)
    ):
        raise _err(source, "'total_duration_range' 는 [최소초, 최대초] 형식이어야 합니다")
    lo, hi = float(range_raw[0]), float(range_raw[1])
    if not (0 < lo <= hi):
        raise _err(source, f"'total_duration_range' [{lo:g}, {hi:g}] 는 0 < 최소 <= 최대 여야 합니다")
    if lo < MIN_TOTAL_DURATION_S or hi > MAX_TOTAL_DURATION_S:
        raise _err(
            source,
            f"'total_duration_range' [{lo:g}, {hi:g}] 는 전역 허용 범위"
            f"({MIN_TOTAL_DURATION_S}~{MAX_TOTAL_DURATION_S}초) 안에 있어야 합니다",
        )
    if not (lo <= total <= hi):
        raise _err(
            source,
            f"총 길이 {total:g}초가 선언된 total_duration_range [{lo:g}, {hi:g}] 를 벗어납니다",
        )

    return Template(
        template_id=template_id,
        name=name,
        version=version,
        mood=mood,
        structure=sections,
        total_duration_range=(lo, hi),
        caption_template=caption_template,
        hashtags_base=tuple(hashtags_raw),
        style_preset=style_preset,
        voice=voice,
        timing=timing,
    )


def load_raw(path: str | Path) -> dict:
    """JSON 파일 하나를 읽어 검증까지 통과한 원본 dict 를 반환한다.

    플랜 영속화(payload 에 템플릿 스냅샷 저장)처럼 원본 dict 가 필요한
    호출자용. 검증은 load_template 과 동일하게 강제한다.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _err(path.name, f"파일을 읽을 수 없습니다 ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _err(path.name, f"JSON 파싱 실패 ({exc})") from exc
    validate_template(data, source=path.name)
    return data


def load_all_raw(directory: str | Path) -> list[dict]:
    """디렉터리의 *.json 템플릿을 검증 후 원본 dict 목록으로 반환한다 (엄격 모드)."""
    directory = Path(directory)
    if not directory.is_dir():
        raise _err(str(directory), "템플릿 디렉터리가 존재하지 않습니다")
    return [
        load_raw(path)
        for path in sorted(directory.glob("*.json"))
        if path.name != "index.json"
    ]


def load_template(path: str | Path) -> Template:
    """JSON 파일 하나를 읽어 검증 후 Template 을 반환한다."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _err(path.name, f"파일을 읽을 수 없습니다 ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _err(path.name, f"JSON 파싱 실패 ({exc})") from exc
    return validate_template(data, source=path.name)


def load_all(directory: str | Path) -> list[Template]:
    """디렉터리의 *.json 템플릿을 파일명 순으로 모두 로드한다 (엄격 모드).

    하나라도 스키마/가드레일 위반이면 TemplateValidationError 를 던진다.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise _err(str(directory), "템플릿 디렉터리가 존재하지 않습니다")
    return [
        load_template(path)
        for path in sorted(directory.glob("*.json"))
        if path.name != "index.json"
    ]
