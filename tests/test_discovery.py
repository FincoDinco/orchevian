import httpx
import pytest

from llm_engine.domain.errors import EngineError
from llm_engine.services.discovery import (
    GIB,
    DiscoveryService,
    Hardware,
    _estimate,
    detect_hardware,
)


def service(handler):
    return DiscoveryService(httpx.Client(transport=httpx.MockTransport(handler)))


def test_search_uses_hub_endpoint_and_format_filter():
    def handler(request):
        assert request.url.path == "/api/models"
        assert request.url.params["search"] == "Qwen"
        assert request.url.params["filter"] == "gguf"
        assert request.url.params["pipeline_tag"] == "text-generation"
        return httpx.Response(200, json=[
            {"id": "owner/Qwen-8B-GGUF", "downloads": 123, "gated": "auto"},
            {"id": "owner/Qwen-8B-GGUF"}, {"id": "https://evil.test"}, None,
        ])

    models = service(handler).search(" Qwen ", "gguf")
    assert len(models) == 1
    assert models[0].downloads == 123 and models[0].gated
    assert models[0].url == "https://huggingface.co/owner/Qwen-8B-GGUF"
    assert "choose a Q4 file" in models[0].estimate_basis


@pytest.mark.parametrize("status", [401, 429, 500])
def test_search_http_errors_are_actionable(status):
    with pytest.raises(EngineError) as exc:
        service(lambda request: httpx.Response(status)).search("", "mlx")
    assert exc.value.code == "search_failed"
    assert "rate limit" in str(exc.value) if status == 429 else "HTTP" in str(exc.value)


def test_search_timeout_and_malformed_response():
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    for handler in [timeout, lambda request: httpx.Response(200, json={"error": "bad"})]:
        with pytest.raises(EngineError, match="Check your connection"):
            service(handler).search("", "gguf")


def test_recommendations_exclude_unknown_oversized_and_incompatible_models():
    api = service(lambda request: httpx.Response(200, json=[
        {"id": "owner/Small-3B-4bit"}, {"id": "owner/Large-70B-4bit"},
        {"id": "owner/Unknown"},
    ]))
    mac = Hardware("Darwin", "arm64", 24 * GIB)
    assert mac.model_budget == int(24 * GIB * 0.7)
    assert [m.repo_id for m in api.recommend(mac, "mlx")] == ["owner/Small-3B-4bit"]
    with pytest.raises(EngineError, match="Choose GGUF"):
        api.recommend(Hardware("Linux", "x86_64", 24 * GIB), "mlx")
    with pytest.raises(EngineError, match="Set a RAM budget"):
        api.recommend(Hardware("Darwin", "arm64", None), "mlx")


def test_estimates_account_for_quantization_and_all_experts():
    assert _estimate("org/Model-8B-4bit", "mlx")[0] < _estimate("org/Model-8B", "mlx")[0]
    assert _estimate("org/Model-8x7B", "gguf")[0] > _estimate("org/Model-7B", "gguf")[0]
    assert _estimate("org/Model-235B-A22B", "gguf")[0] > 100 * GIB
    assert _estimate("org/Model", "gguf")[0] is None


def test_hardware_detection_fails_without_inventing_ram(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")

    def denied(*args, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr("psutil.virtual_memory", denied)
    hardware = detect_hardware()
    assert hardware.memory_bytes is None and hardware.model_budget is None
    assert "could not be detected" in hardware.summary
