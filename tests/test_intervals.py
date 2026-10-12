from datetime import date

import pytest
import requests

from garmin_intervals_bridge.intervals import IntervalsClient


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.content = b"{}" if payload is not None else b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("API error")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.auth = None
        self.calls = []
        self.items = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/custom-item") and method == "GET":
            return Response(self.items)
        return Response({"id": "x"})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return Response({"id": "2026-10-08", "Existing": 1})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return Response([{"id": "i999"}], status=201)


def test_api_auth_and_paths():
    fake = FakeSession()
    api = IntervalsClient("secret", session=fake)
    assert fake.auth == ("API_KEY", "secret")
    assert api.wellness(date(2026, 10, 8))["Existing"] == 1
    api.write_wellness(date(2026, 10, 8), {"readiness": 44})
    method, url, req = fake.calls[-1]
    assert method == "PUT" and url.endswith("/wellness/2026-10-08")
    assert req["json"] == {"readiness": 44}


def test_private_custom_item_provision_idempotence():
    fake = FakeSession()
    fake.items = [{"type": "INPUT_FIELD", "content": {"code": "BodyBatteryMax", "type": "numeric"}}]
    api = IntervalsClient("secret", session=fake)
    plan = api.provision_fields(apply=False, needed={"GarminTrainingReadiness", "BodyBatteryMax"})
    assert plan == ["GarminTrainingReadiness"]
    assert all(method != "POST" for method, *_ in fake.calls)
    api.provision_fields(apply=True, needed={"GarminTrainingReadiness"})
    calls = [req for method, _, req in fake.calls if method == "POST"]
    assert len(calls) == 1
    assert calls[0]["json"]["visibility"] == "PRIVATE"
    assert calls[0]["json"]["content"]["type"] == "numeric"


def test_activity_upload_201_means_created(tmp_path):
    fake = FakeSession()
    api = IntervalsClient("secret", session=fake)
    fit_path = tmp_path / "123.fit"
    fit_path.write_bytes(b"synthetic-upload-body")
    result = api.upload_fit("123", fit_path)
    assert result["created"] is True
    assert result["items"][0]["id"] == "i999"
    method, url, kwargs = fake.calls[-1]
    assert method == "POST" and kwargs["params"]["external_id"] == "garmin:123"


def test_activity_upload_200_means_no_activity_created(tmp_path):
    class DuplicateSession(FakeSession):
        def post(self, url, **kwargs):
            return Response([], status=200)
    api = IntervalsClient("secret", session=DuplicateSession())
    path = tmp_path / "123.fit"
    path.write_bytes(b"test bytes")
    assert api.upload_fit("123", path) == {"created": False, "items": []}


def test_activity_upload_current_openapi_response(tmp_path):
    class CurrentResponseSession(FakeSession):
        def post(self, url, **kwargs):
            return Response({"icu_athlete_id": "i567", "id": "garmin:123",
                             "activities": [{"id": "i999"}]}, status=201)

    api = IntervalsClient("secret", session=CurrentResponseSession())
    path = tmp_path / "123.fit"
    path.write_bytes(b"synthetic test")
    out = api.upload_fit("123", path)
    assert out == {"created": True, "items": [{"id": "i999"}]}

def test_activity_upload_bad_current_response_fails_closed(tmp_path):
    class BrokenResponseSession(FakeSession):
        def post(self, url, **kwargs):
            return Response({"icu_athlete_id": "i567", "id": "garmin:123"}, status=201)

    api = IntervalsClient("secret", session=BrokenResponseSession())
    path = tmp_path / "123.fit"
    path.write_bytes(b"synthetic test")
    with pytest.raises(ValueError, match="activities missing"):
        api.upload_fit("123", path)


def test_a_wellness_write_answered_with_429_waits_and_is_sent_again(monkeypatch):
    from garmin_intervals_bridge import intervals as module

    class Limited(Response):
        def __init__(self):
            super().__init__(None, 429)
            self.headers = {"Retry-After": "2"}

        def raise_for_status(self):
            raise requests.HTTPError("429 Too Many Requests", response=self)

    class Session(FakeSession):
        answers = [Limited(), Response({"id": "2026-10-08"})]

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return self.answers.pop(0)

    waits = []
    monkeypatch.setattr(module.time, "sleep", waits.append)
    session = Session()
    client = IntervalsClient("k", session=session)
    assert client.write_wellness(date(2026, 10, 8), {"HRVTrendAdvice": 3}) == {"id": "2026-10-08"}
    assert waits == [2.0] and [c[0] for c in session.calls] == ["PUT", "PUT"]
    assert session.calls[0][2]["json"] == session.calls[1][2]["json"] == {"HRVTrendAdvice": 3}
