import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, LocalModel, ModelRef
from llm_engine.services.discovery import GIB, Hardware, RemoteModel, quantization_bits
from llm_engine.services.downloads import DownloadChoice, DownloadPlan, HubFile
from llm_engine.services.ollama_downloads import OllamaDownloadService
from llm_manager_app.widgets.model_discovery import ModelDiscovery
from test_models_view import _qapp, _trigger, _window


@pytest.mark.parametrize("name,bits", [
    ("model-Q4_K_M.gguf", 4), ("model.IQ3_XXS.gguf", 3),
    ("mlx-community/Qwen3-4B-8bit", 8), ("model-BF16.gguf", 16),
    ("model-fp16", 16), ("Qwen3-4B", None), ("model-Q6_K.gguf", 6),
])
def test_precision_names(name, bits):
    assert quantization_bits(name) == bits


def test_quantization_switch_does_not_reuse_other_download_state():
    _qapp()
    view = ModelDiscovery()
    model = RemoteModel("org/model-GGUF", "gguf", 0, None, "")
    choices = tuple(DownloadChoice(f"model-Q{bits}_0.gguf", (HubFile("weights", bits * GIB),))
                    for bits in (4, 8))
    plan = DownloadPlan(model, "a" * 40, choices)
    view.apply_results([model], Hardware("Linux", "x86_64", 32 * GIB), False)
    view.apply_plan(plan)
    assert not view.choices.isHidden()
    view.update_downloads([SimpleNamespace(key=(model.repo_id, "gguf", choices[0].name),
                                          state="Downloading")])
    assert view.download.text() == "View downloads"
    emitted = []
    view.download_requested.connect(lambda *args: emitted.append(args))
    view.choices.setCurrentIndex(1)
    view.download.click()
    assert emitted[0][1] == choices[1]
    view.precision.setCurrentIndex(view.precision.findData(4))
    assert view.choices.count() == 1
    assert view.download.text() == "View downloads"
    view.precision.setCurrentIndex(view.precision.findData(6))
    assert view.choices.count() == 0
    assert not view.download.isEnabled()
    view.close()


def test_ollama_mode_exposes_tag_input_without_hugging_face_filters():
    _qapp()
    view = ModelDiscovery()
    view.format.setCurrentIndex(view.format.findData("ollama"))
    assert "model:tag" in view.query.placeholderText()
    assert not view.precision.isEnabled()
    assert not view.recommended.isEnabled()
    assert view.search.text() == "Choose tag"
    view.close()


def test_ollama_tag_download_reaches_library_through_background_queue(tmp_path):
    _qapp()
    backend = FakeBackend(models=[])
    window, store, _ = _window(tmp_path, BackendRegistry([backend]))
    view = window._models
    local = LocalModel(ModelRef(BackendName.OLLAMA, "qwen3:4b"), None, GIB)

    class Downloads(OllamaDownloadService):
        def download(self, plan, choice, cancel, progress, token=""):
            assert choice.name == local.ref.name
            progress(GIB, GIB, "success")
            backend._models.append(local)
            return local.ref

    view._ollama_downloads = Downloads()
    try:
        view._discovery.format.setCurrentIndex(view._discovery.format.findData("ollama"))
        view._discovery.query.setText("qwen3:4b")
        _trigger(view.job_finished, view._discovery.search.click)
        assert view._discovery.download.isEnabled()
        _trigger(view.refreshed, view._discovery.download.click)
        assert view.selected_model().ref == local.ref
        assert view.downloads.jobs[1].state == "Complete"
        assert view.download_view._rows[1][1].value() == 1000
    finally:
        window.close()
        store.close()


def make_plan(service):
    return service.prepare(RemoteModel("qwen3:4b", "ollama", 0, None, ""))


def test_ollama_pull_stream_and_progress():
    def handle(request):
        assert request.url.path == "/api/pull"
        assert json.loads(request.content) == {"model": "qwen3:4b", "stream": True}
        return httpx.Response(200, text='\n'.join(json.dumps(item) for item in [
            {"status": "pulling manifest"},
            {"status": "pulling", "digest": "abc", "total": 100, "completed": 40},
            {"status": "pulling", "digest": "abc", "total": 100, "completed": 100},
            {"status": "success"},
        ]))
    with httpx.Client(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(handle),
    ) as client:
        service = OllamaDownloadService(client)
        plan = make_plan(service)
        progress = []
        ref = service.download(plan, plan.choices[0], threading.Event(),
                               lambda *args: progress.append(args))
        assert ref.id == "ollama/qwen3:4b"
        assert progress[1][:2] == (40, 100)
        assert progress[-1][:2] == (100, 100)


@pytest.mark.parametrize("body", ['{"error":"unknown tag"}', '{"status":"pulling manifest"}'])
def test_ollama_pull_requires_success(body):
    with httpx.Client(base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=body)
    )) as client:
        service = OllamaDownloadService(client)
        plan = make_plan(service)
        with pytest.raises(EngineError):
            service.download(plan, plan.choices[0], threading.Event(), lambda *args: None)


def test_ollama_cancel_before_download_never_makes_request():
    def handle(request):
        pytest.fail("Cancelled download made a request")
    with httpx.Client(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(handle),
    ) as client:
        service = OllamaDownloadService(client)
        plan = make_plan(service)
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(EngineError, match="cancelled"):
            service.download(plan, plan.choices[0], cancel, lambda *args: None)


def test_ollama_cancel_closes_a_waiting_download_stream():
    cancel, closed = threading.Event(), threading.Event()

    class WaitingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"status":"pulling manifest"}\n'
            assert closed.wait(2), "Cancellation did not close the response"

        def close(self):
            closed.set()

    with httpx.Client(base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=WaitingStream())
    )) as client:
        service = OllamaDownloadService(client)
        plan = make_plan(service)
        with pytest.raises(EngineError) as error:
            service.download(plan, plan.choices[0], cancel, lambda *args: cancel.set())
        assert error.value.code == "cancelled"
        assert closed.is_set()
