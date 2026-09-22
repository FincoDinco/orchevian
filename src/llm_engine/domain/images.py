"""Image payload bounds shared by services and inference adapters."""
from llm_engine.domain.errors import EngineError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TURN_IMAGES = 4


def validate_image_inputs(messages):
    images = [data for turn in messages for data in turn.images]
    if len(images) > MAX_TURN_IMAGES:
        raise EngineError("document_failed", "Use at most 4 images in one model request.")
    if any(turn.images and turn.role != "user" for turn in messages):
        raise EngineError("document_failed", "Images must belong to a user message.")
    if any(not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES for data in images):
        raise EngineError(
            "document_failed", "Each processed image must be between 1 byte and 2 MB."
        )

