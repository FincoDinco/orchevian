from pathlib import Path

from .config import get_config


def search_models(query: str, limit: int = 20) -> list[dict]:
    from huggingface_hub import list_models

    results = []
    for m in list_models(search=query, limit=limit, sort="downloads"):
        results.append({
            "id": m.id,
            "downloads": getattr(m, "downloads", 0) or 0,
            "likes": getattr(m, "likes", 0) or 0,
            "tags": getattr(m, "tags", []) or [],
            "pipeline_tag": getattr(m, "pipeline_tag", "") or "",
        })
    return results


def list_gguf_files(repo_id: str) -> list[str]:
    """Return the .gguf filenames published in a repo, smallest-name-first."""
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo_id)
    names = [s.rfilename for s in info.siblings if s.rfilename.lower().endswith(".gguf")]
    return sorted(names)


def pull_mlx(repo_id: str, progress_callback=None) -> Path:
    from huggingface_hub import snapshot_download

    name = repo_id.replace("/", "--")
    dest = get_config().mlx_dir / name

    def _cb(info):
        if progress_callback and info.get("total"):
            progress_callback(int(info["downloaded"] / info["total"] * 100))

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest),
        ignore_patterns=["*.bin", "*.pt", "original/*", "*.ot"],
    )
    return dest


def pull_gguf(repo_id: str, filename: str, progress_callback=None) -> Path:
    from huggingface_hub import hf_hub_download

    dest_dir = get_config().gguf_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(dest_dir),
    )
    return Path(path)


def get_model_card(repo_id: str) -> str:
    try:
        from huggingface_hub import ModelCard
        card = ModelCard.load(repo_id)
        return card.text or ""
    except Exception:
        return ""
