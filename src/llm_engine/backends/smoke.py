"""Spawn-safe fake vision backend used by desktop bundle validation."""

from dataclasses import replace

from llm_engine.backends.fake import FakeBackend


class VisionSmokeBackend(FakeBackend):
    def load(self, model, options=None):
        return super().load(replace(model, supports_images=True), options)

    def stream_generate(self, handle, messages, params, cancel):
        if not messages[-1].images[0].startswith(b"\xff\xd8"):
            raise RuntimeError("Image bytes did not reach the model process")
        yield "Visual smoke passed"
