"""트렌드 템플릿 JSON 스키마 정의 + 로더.

템플릿 한 개는 아래 필드를 가진 JSON 오브젝트다.

- template_id: 고유 식별자 (비어있지 않은 문자열)
- name: 한국어 표시 이름
- version: int >= 1
- mood: "upbeat" | "calm" | "energetic" (BGM 트랙 팩 mood 3종과 동일 — docs/TRACK_PACK.md)
- structure: 순서 있는 섹션 배열. 각 섹션은
    role: "hook" | "body" | "cta"
    duration_s: 양수 (초)
    script_guide: 한국어 가이드 문구
    material_slot: "photo" | "video" | "any"
- total_duration_range: [최소초, 최대초]
- caption_template: 캡션 템플릿 문자열
- hashtags_base: 기본 해시태그 문자열 배열

전역 가드레일 (위반 시 TemplateValidationError, 사유 포함):
- 섹션 duration_s 합산이 10~60초 범위
- hook 섹션 필수이며 첫 번째 섹션이어야 함
- cta 섹션 필수
- 섹션 개수 >= 2
- mood 는 유효값 3종 중 하나
- version 은 int >= 1
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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


class TemplateValidationError(ValueError):
    """템플릿 스키마/가드레일 위반. 메시지에 위반 사유를 포함한다."""


@dataclass(frozen=True)
class Section:
    role: str
    duration_s: float
    script_guide: str
    material_slot: str


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


def _parse_section(raw: object, index: int, source: str) -> Section:
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

    return Section(
        role=role,
        duration_s=float(duration_s),
        script_guide=script_guide,
        material_slot=material_slot,
    )


def validate_template(data: object, source: str = "<template>") -> Template:
    """템플릿 dict 를 검증하고 Template 로 변환한다. 위반 시 TemplateValidationError."""
    if not isinstance(data, dict):
        raise _err(source, "템플릿 루트는 JSON 오브젝트여야 합니다")

    template_id = _require_str(data, "template_id", source)
    name = _require_str(data, "name", source)
    caption_template = _require_str(data, "caption_template", source)

    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _err(source, f"'version' 은 1 이상의 정수여야 합니다 (현재: {version!r})")

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
        _parse_section(raw, index, source) for index, raw in enumerate(structure_raw)
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
    )


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
