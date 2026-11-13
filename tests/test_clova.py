"""core.clova.chat 의 시간 제한 인자. 실제 호출 없이 _post 를 바꿔 끼운다."""

from core import clova


def test_chat_timeout_default_and_override(monkeypatch):
    seen = []

    def fake_post(path, payload, timeout):
        seen.append(timeout)
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(clova, "_post", fake_post)
    assert clova.chat("s", "u") == "ok"
    clova.chat("s", "u", timeout=20)
    assert seen == [15, 20]
