"""네이버 데이터랩 수요 신호(app.promo.research.datalab) 테스트.

urllib 호출은 전부 mock 처리한다 (실 네트워크 0).
"""

import json
from datetime import date

import pytest

from app.config import config
from app.promo.research import datalab


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _response_for(keywords, ratio=50.0):
    return _FakeResponse(
        {
            "results": [
                {"title": keyword, "data": [{"period": "2026-08-01", "ratio": ratio}]}
                for keyword in keywords
            ]
        }
    )


@pytest.fixture()
def naver_keys(monkeypatch):
    monkeypatch.setitem(config.app, "naver_client_id", "test-id")
    monkeypatch.setitem(config.app, "naver_client_secret", "test-secret")


def test_configured_false_without_keys(monkeypatch):
    monkeypatch.setitem(config.app, "naver_client_id", "")
    monkeypatch.setitem(config.app, "naver_client_secret", "")
    assert datalab.configured() is False


def test_configured_true_with_keys(naver_keys):
    assert datalab.configured() is True


def test_fetch_demand_not_configured(monkeypatch):
    monkeypatch.setitem(config.app, "naver_client_id", "")
    monkeypatch.setitem(config.app, "naver_client_secret", "")
    with pytest.raises(datalab.DataLabNotConfiguredError):
        datalab.fetch_demand(["무선 선풍기"])


def test_fetch_demand_requires_keywords(naver_keys):
    with pytest.raises(ValueError):
        datalab.fetch_demand(["  "])


def test_fetch_demand_builds_request_payload(naver_keys, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured.update(json.loads(request.data.decode("utf-8")))
        return _response_for(["무선 선풍기"])

    monkeypatch.setattr(datalab.urllib.request, "urlopen", fake_urlopen)

    demand = datalab.fetch_demand(["무선 선풍기"], days=28, today=date(2026, 8, 15))

    assert demand == {"무선 선풍기": 50.0}
    assert captured["startDate"] == "2026-07-18"
    assert captured["endDate"] == "2026-08-15"
    assert captured["timeUnit"] == "date"
    assert captured["group"] == [
        {"groupName": "무선 선풍기", "keywords": ["무선 선풍기"]}
    ]


def test_fetch_demand_averages_ratios(naver_keys, monkeypatch):
    monkeypatch.setattr(
        datalab.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            {
                "results": [
                    {
                        "title": "무선 선풍기",
                        "data": [{"ratio": 10}, {"ratio": 20}, {"ratio": 30}],
                    }
                ]
            }
        ),
    )

    assert datalab.fetch_demand(["무선 선풍기"], today=date(2026, 8, 15)) == {
        "무선 선풍기": 20.0
    }


def test_fetch_demand_empty_data_is_zero(naver_keys, monkeypatch):
    monkeypatch.setattr(
        datalab.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            {"results": [{"title": "무선 선풍기", "data": []}]}
        ),
    )

    assert datalab.fetch_demand(["무선 선풍기"], today=date(2026, 8, 15)) == {
        "무선 선풍기": 0.0
    }


def test_fetch_demand_drops_keywords_without_results(naver_keys, monkeypatch):
    monkeypatch.setattr(
        datalab.urllib.request,
        "urlopen",
        lambda request, timeout=None: _response_for(["무선 선풍기"]),
    )

    demand = datalab.fetch_demand(
        ["무선 선풍기", "없는 키워드"], today=date(2026, 8, 15)
    )

    assert demand == {"무선 선풍기": 50.0}


def test_fetch_demand_chunks_by_five(naver_keys, monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append(payload)
        return _response_for([group["groupName"] for group in payload["group"]])

    monkeypatch.setattr(datalab.urllib.request, "urlopen", fake_urlopen)

    keywords = [f"키워드{i}" for i in range(7)]
    demand = datalab.fetch_demand(keywords, today=date(2026, 8, 15))

    assert len(calls) == 2
    assert len(calls[0]["group"]) == 5
    assert len(calls[1]["group"]) == 2
    assert len(demand) == 7


def test_fetch_demand_network_error(naver_keys, monkeypatch):
    def raise_oserror(request, timeout=None):
        raise OSError("boom")

    monkeypatch.setattr(datalab.urllib.request, "urlopen", raise_oserror)

    with pytest.raises(datalab.DataLabFetchError):
        datalab.fetch_demand(["무선 선풍기"], today=date(2026, 8, 15))


def test_fetch_demand_includes_http_error_body(naver_keys, monkeypatch):
    """네이버 오류 본문(NID AUTH 등)이 예외 메시지에 드러나야 진단 가능하다."""
    import io
    import urllib.error

    def raise_http(request, timeout=None):
        raise urllib.error.HTTPError(
            datalab.DATALAB_URL,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"errorCode": "024", "errorMessage": "NID AUTH failed"}'),
        )

    monkeypatch.setattr(datalab.urllib.request, "urlopen", raise_http)

    with pytest.raises(datalab.DataLabFetchError, match="HTTP 401") as excinfo:
        datalab.fetch_demand(["무선 선풍기"], today=date(2026, 8, 15))
    assert "NID AUTH" in str(excinfo.value)
