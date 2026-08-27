def format_bytes(size_bytes: int) -> str:
    """
    Converts a byte count to a human-readable string.
    """
    if size_bytes < 0:
        raise ValueError("Byte count cannot be negative")
    
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}".replace(".00", "").strip()
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"
