from llm_manager.format import format_bytes


def test_format_bytes_values():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1 KB"
    assert format_bytes(1024**2) == "1 MB"
    assert format_bytes(1024**3) == "1 GB"
    assert format_bytes(1500000000) == "1.40 GB"
