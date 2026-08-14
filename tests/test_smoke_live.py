"""실 API(OpenDART/KRX/뉴스/Claude) 스모크 테스트 — opt-in.

기본적으로 SKIP된다. `RUN_LIVE=1` 환경변수가 설정된 경우에만 실행되며,
OPENDART_API_KEY(필수, DartClient가 내부적으로 요구) 및 선택적으로
ANTHROPIC_API_KEY가 있으면 6단계 파이프라인(뉴스·요약 포함) 전체를 실제
네트워크로 실행해 핵심 PER 필드가 채워지는지 확인한다.

뉴스/LLM 파트는 외부 사이트 마크업 변경이나 요약 실패로도 흔들릴 수 있으므로,
그 부분은 "동작은 하되 assert는 관대하게"만 검증한다 — insights.status가
정의된 값 중 하나이기만 하면 되고, 내용 자체는 검증하지 않는다.
"""
import os
import pytest


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="실 API 스모크는 RUN_LIVE=1 일 때만")
def test_live_vt():
    from app.dart_client import DartClient
    from app.krx_client import KrxClient
    from app.news_client import NewsClient
    from app import pipeline, insights

    res = pipeline.run_analysis(
        "브이티", DartClient(), KrxClient(),
        news=NewsClient(), insights_fn=insights.summarize,
    )

    # 핵심 PER 필드 — 실데이터 기반이라도 반드시 채워져야 하는 부분
    assert res["target"]["name"]
    assert "per_op" in res["target"]
    assert len(res["peers"]) >= 1

    # 뉴스·요약(6단계)은 동작만 확인 — 내용/개수는 외부 소스에 따라 흔들릴 수 있어 검증 안 함
    assert res["insights"]["status"] in ("ok", "no_data", "disabled")
