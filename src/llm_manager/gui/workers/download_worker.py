from PySide6.QtCore import QThread, Signal


class DownloadThread(QThread):
    progress = Signal(int, str)   # percent, status message
    finished = Signal(str)        # result path
    error_occurred = Signal(str)

    def __init__(self, backend: str, repo_id: str, filename: str = ""):
        super().__init__()
        self._backend = backend
        self._repo_id = repo_id
        self._filename = filename

    def run(self):
        try:
            from llm_manager import hub
            if self._backend == "mlx":
                self.progress.emit(0, f"Downloading {self._repo_id} from HuggingFace…")
                path = hub.pull_mlx(
                    self._repo_id,
                    progress_callback=lambda p: self.progress.emit(p, f"{p}% downloaded"),
                )
                self.finished.emit(str(path))

            elif self._backend == "gguf":
                self.progress.emit(0, f"Downloading {self._filename}…")
                path = hub.pull_gguf(
                    self._repo_id,
                    self._filename,
                    progress_callback=lambda p: self.progress.emit(p, f"{p}% downloaded"),
                )
                self.finished.emit(str(path))

            elif self._backend == "ollama":
                from llm_manager.backends import get_backend
                backend = get_backend("ollama")
                for status in backend.pull(self._repo_id):
                    self.progress.emit(-1, status)
                self.finished.emit(self._repo_id)

        except Exception as e:
            self.error_occurred.emit(str(e))
