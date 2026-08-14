def normalize_name(value: str) -> str:
    """返回小写且去除两端空白的名称。

    >>> normalize_name(" Rook ")
    'ROOK'
    """
    return value.strip().lower()
