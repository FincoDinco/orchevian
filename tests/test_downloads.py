from __future__ import annotations

import hashlib
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from llm_engine.backends.gguf import GGUFBackend
from llm_engine.backends.mlx import MLXBackend
from llm_engine.domain.errors import EngineError
from llm_engine.services.discovery import RemoteModel
from llm_engine.services.downloads import DownloadService

REVISION = "a" * 40


def _service(tmp_path, files, *, format="gguf", metadata=None, respond=None):
    requests = []
    model = RemoteModel("owner/Tiny-1B", format, 100, 3_000_000_000, "Estimated")

    def handler(request):
        requests.append(request)
        if request.url.path.startswith("/api/"):
            return httpx.Response(200, json=metadata if metadata is not None else {
                "sha": REVISION,
                "siblings": [
                    {"rfilename": name, "size": len(body), "lfs": {
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }} for name, body in files.items()
                ],
            })
        assert f"/resolve/{REVISION}/" in request.url.path
        name = request.url.path.split(f"/resolve/{REVISION}/")[1]
        return respond(request) if respond else httpx.Response(200, content=files[name])

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return DownloadService(tmp_path, client), model, requests


def _download(service, plan, *, cancel=None, progress=None):
    return service.download(
        plan, plan.choices[0], cancel or threading.Event(), progress or (lambda *args: None),
    )


@pytest.mark.parametrize("filename", ["Tiny-Q4_K_M.gguf", "Tiny-Q4_K_M.GGUF"])
def test_download_is_pinned_verified_hidden_until_complete_and_reinstallable(tmp_path, filename):
    service, model, requests = _service(tmp_path, {filename: b"weights"})
    plan = service.prepare(model, token="test-token")
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].url.params["blobs"] == "true"
    backend = GGUFBackend(tmp_path / "gguf")
    updates = []

    def progress(done, total, name):
        assert backend.list_models() == []
        updates.append((done, total, name))

    ref = _download(service, plan, progress=progress)
    installed = backend.list_models()
    assert len(installed) == 1 and installed[0].ref == ref
    assert installed[0].path.read_bytes() == b"weights"
    assert updates[-1][:2] == (7, 7)
    with pytest.raises(EngineError, match="Already downloaded"):
        _download(service, plan)
    backend.delete(installed[0])
    assert _download(service, plan) == ref
    assert not list((tmp_path / "gguf").glob(".download-*"))


def test_split_gguf_is_one_model_and_deletes_all_shards(tmp_path):
    files = {f"Tiny-Q4-0000{i}-of-00002.gguf": b"123" for i in (1, 2)}
    service, model, _ = _service(tmp_path, files)
    plan = service.prepare(model)
    assert len(plan.choices) == 1 and len(plan.choices[0].files) == 2
    ref = _download(service, plan)
    backend = GGUFBackend(tmp_path / "gguf")
    installed = backend.list_models()
    assert len(installed) == 1 and installed[0].ref == ref
    assert installed[0].size_bytes == 6
    backend.delete(installed[0])
    assert not list(tmp_path.rglob("*.gguf"))
    assert _download(service, plan) == ref


def test_incomplete_gguf_shards_are_not_offered(tmp_path):
    service, model, _ = _service(tmp_path, {"Tiny-00001-of-00002.gguf": b"1"})
    with pytest.raises(EngineError, match="No complete"):
        service.prepare(model)


@pytest.mark.parametrize("body", [b"short", b"WRONG!!", b"too many bytes"])
def test_corrupt_downloads_are_removed(tmp_path, body):
    service, model, _ = _service(
        tmp_path, {"tiny.gguf": b"weights"},
        respond=lambda request: httpx.Response(200, content=body),
    )
    with pytest.raises(EngineError, match="mismatch|verification"):
        _download(service, service.prepare(model))
    assert list((tmp_path / "gguf").iterdir()) == []


def test_cancellation_cleans_partial_files_and_can_retry(tmp_path):
    service, model, _ = _service(tmp_path, {"tiny.gguf": b"weights"})
    plan = service.prepare(model)
    cancel = threading.Event()

    def progress(done, total, name):
        if done:
            cancel.set()

    with pytest.raises(EngineError) as caught:
        _download(service, plan, cancel=cancel, progress=progress)
    assert caught.value.code == "cancelled"
    assert list((tmp_path / "gguf").iterdir()) == []
    _download(service, plan)


def test_disk_space_checked_before_fetching_weights(tmp_path, monkeypatch):
    service, model, requests = _service(tmp_path, {"tiny.gguf": b"weights"})
    plan = service.prepare(model)
    monkeypatch.setattr("llm_engine.services.downloads.shutil.disk_usage",
                        lambda path: SimpleNamespace(free=0))
    with pytest.raises(EngineError) as caught:
        _download(service, plan)
    assert caught.value.code == "disk_full"
    assert len(requests) == 1


@pytest.mark.parametrize("metadata", [
    {"sha": None}, {"sha": 123}, {"sha": REVISION, "siblings": None},
    {"sha": REVISION, "siblings": [{"rfilename": "tiny.gguf", "size": True}]},
    {"sha": REVISION, "siblings": [{"rfilename": "tiny.gguf", "size": 1,
                                     "lfs": {"sha256": 123}}]},
])
def test_malformed_metadata_has_actionable_error(tmp_path, metadata):
    service, model, _ = _service(tmp_path, {}, metadata=metadata)
    with pytest.raises(EngineError):
        service.prepare(model)


@pytest.mark.parametrize("name", ["../tiny.gguf", "a/../../tiny.gguf", "C:/tiny.gguf",
                                  "CON.gguf", "a\\tiny.gguf", "a//tiny.gguf"])
def test_unsafe_repository_paths_rejected(tmp_path, name):
    service, model, _ = _service(tmp_path, {name: b"1"})
    with pytest.raises(EngineError):
        service.prepare(model)


def test_symlink_destination_outside_model_folder_rejected(tmp_path):
    service, model, _ = _service(tmp_path, {"tiny.gguf": b"1"})
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / "gguf").mkdir()
    try:
        (tmp_path / "gguf" / "owner").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(EngineError, match="outside"):
        _download(service, service.prepare(model))
    assert list(outside.iterdir()) == []


def test_mlx_snapshot_contains_weights_config_tokenizer_and_template(tmp_path):
    files = {"config.json": b"{}", "model.safetensors": b"weights", "tokenizer.json": b"{}",
             "chat_templates/default.jinja": b"template", "custom_code.py": b"ignored"}
    service, model, _ = _service(tmp_path, files, format="mlx")
    ref = _download(service, service.prepare(model))
    installed = MLXBackend(tmp_path / "mlx").list_models()
    assert len(installed) == 1 and installed[0].ref == ref
    assert (installed[0].path / "chat_templates/default.jinja").is_file()
    assert not (installed[0].path / "custom_code.py").exists()


def test_mlx_missing_indexed_weights_rejected(tmp_path):
    files = {"config.json": b"{}", "model.safetensors": b"weights", "tokenizer.json": b"{}",
             "model.safetensors.index.json": json.dumps({
                 "weight_map": {"layer": "missing.safetensors"},
             }).encode()}
    service, model, _ = _service(tmp_path, files, format="mlx")
    with pytest.raises(EngineError, match="missing weights"):
        _download(service, service.prepare(model))
    assert MLXBackend(tmp_path / "mlx").list_models() == []


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_http_failures_do_not_expose_credentials(tmp_path, status):
    service, model, _ = _service(tmp_path, {"tiny.gguf": b"1"}, respond=lambda request:
                                httpx.Response(status, text="signed-url-secret"))
    with pytest.raises(EngineError) as caught:
        _download(service, service.prepare(model))
    assert "secret" not in str(caught.value) and "https://" not in str(caught.value)
    assert list((tmp_path / "gguf").iterdir()) == []


def test_cross_host_redirect_does_not_forward_token(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "test-secret")
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"location": "https://cdn.example/weights"})
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=b"1")

    service, model, _ = _service(tmp_path, {"tiny.gguf": b"1"})
    plan = service.prepare(model)
    service._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    _download(service, plan)
    assert seen[0].headers["Authorization"] == "Bearer test-secret"
