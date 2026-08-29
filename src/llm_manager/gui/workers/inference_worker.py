import time

from PySide6.QtCore import QObject, QThread, Signal


class InferenceWorker(QObject):
    token_received = Signal(str)
    finished = Signal(float, float, int)  # tps, elapsed, token_count
    error_occurred = Signal(str)

    def __init__(self, backend, model, messages: list[dict], params: dict):
        super().__init__()
        self._backend = backend
        self._model = model
        self._messages = messages
        self._params = params
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        start = time.time()
        count = 0
        try:
            for token in self._backend.stream_chat(self._model, self._messages, self._params):
                if self._stop:
                    break
                self.token_received.emit(token)
                count += 1
            elapsed = time.time() - start
            tps = count / elapsed if elapsed > 0 else 0.0
            self.finished.emit(tps, elapsed, count)
        except Exception as e:
            self.error_occurred.emit(str(e))


class InferenceThread(QThread):
    token_received = Signal(str)
    finished = Signal(float, float, int)
    error_occurred = Signal(str)

    def __init__(self, backend, model, messages: list[dict], params: dict):
        super().__init__()
        self._backend = backend
        self._model = model
        self._messages = messages
        self._params = params
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        start = time.time()
        count = 0
        try:
            for token in self._backend.stream_chat(self._model, self._messages, self._params):
                if self._stop:
                    break
                self.token_received.emit(token)
                count += 1
            elapsed = time.time() - start
            tps = count / elapsed if elapsed > 0 else 0.0
            self.finished.emit(tps, elapsed, count)
        except Exception as e:
            self.error_occurred.emit(str(e))
