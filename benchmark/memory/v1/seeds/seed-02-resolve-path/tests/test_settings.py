from rook_seed.config.settings_impl import service_url


def test_service_url_removes_trailing_slash() -> None:
    assert service_url("https://api.example.com/", 443) == "https://api.example.com:443"


def test_service_url_keeps_plain_host() -> None:
    assert service_url("http://localhost", 8080) == "http://localhost:8080"
