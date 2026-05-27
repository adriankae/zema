from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from czm_cli import cli as cli_module
from czm_cli.errors import EXIT_CONFLICT, EXIT_USAGE


class FakeClient:
    def __init__(self, responses: dict[tuple[str, str], object]):
        self.responses = responses
        self.requests = []

    def get(self, path, params=None):
        self.requests.append(("GET", path, params))
        response = self.responses[("GET", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, path, json=None, params=None):
        self.requests.append(("POST", path, json))
        response = self.responses[("POST", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def patch(self, path, json=None, params=None):
        self.requests.append(("PATCH", path, json))
        response = self.responses[("PATCH", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def delete(self, path, json=None, params=None):
        self.requests.append(("DELETE", path, json))
        response = self.responses[("DELETE", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def upload_file(self, path, *, field_name, file_path, content_type=None):
        self.requests.append(("UPLOAD", path, field_name, str(file_path), content_type))
        response = self.responses[("UPLOAD", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def download_file(self, path):
        self.requests.append(("DOWNLOAD", path))
        response = self.responses[("DOWNLOAD", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


class DummyConfig:
    base_url = "http://example"
    api_key = "k"
    timezone = "UTC"

    def normalized_base_url(self):
        return "http://example"


def test_cli_json_output(monkeypatch, capsys):
    fake = FakeClient(
        {
            ("GET", "/subjects"): {"subjects": [{"id": 1, "display_name": "Child"}]},
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())
    exit_code = cli_module.main(["--json", "--base-url", "http://example", "--api-key", "k", "subject", "list"])
    assert exit_code == 0
    output = capsys.readouterr().out.strip()
    assert json.loads(output) == {"subjects": [{"id": 1, "display_name": "Child"}]}


def test_cli_missing_config_exit_code(monkeypatch, capsys):
    from pathlib import Path

    monkeypatch.delenv("CZM_BASE_URL", raising=False)
    monkeypatch.delenv("CZM_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", cli_module.resolve_runtime_config)
    monkeypatch.setattr("czm_cli.cli.resolve_runtime_config", cli_module.resolve_runtime_config)
    monkeypatch.setattr("czm_cli.config.xdg_config_path", lambda: Path("/tmp/nonexistent-czm-config.toml"))
    exit_code = cli_module.main(["subject", "list"])
    assert exit_code == EXIT_USAGE
    assert "missing required configuration" in capsys.readouterr().err


def test_cli_conflict_exit_code_json(monkeypatch, capsys):
    fake = FakeClient({("GET", "/subjects"): cli_module.CzmError("conflict happened", exit_code=EXIT_CONFLICT)})
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())
    exit_code = cli_module.main(["--json", "--base-url", "http://example", "--api-key", "k", "subject", "list"])
    assert exit_code == EXIT_CONFLICT
    assert json.loads(capsys.readouterr().out) == {"error": {"code": "conflict", "message": "conflict happened"}}


def test_cli_json_preserves_datetime_payloads(monkeypatch, capsys):
    fake = FakeClient(
        {
            ("GET", "/episodes/1"): {
                "episode": {
                    "id": 1,
                    "subject_id": 1,
                    "location_id": 1,
                    "status": "active_flare",
                    "current_phase_number": 1,
                    "phase_started_at": datetime(2026, 4, 15, 23, 30, tzinfo=timezone.utc),
                    "phase_due_end_at": None,
                    "protocol_version": "v1",
                    "healed_at": None,
                    "obsolete_at": None,
                }
            }
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())
    exit_code = cli_module.main(["--json", "--base-url", "http://example", "--api-key", "k", "episode", "get", "1"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["episode"]["phase_started_at"] == "2026-04-15T23:30:00Z"


def test_cli_setup_command(monkeypatch, tmp_path, capsys):
    from czm_cli.commands import setup as setup_module

    config_path = tmp_path / "config.toml"

    def fake_bootstrap_config(**kwargs):
        config_path.write_text("base_url = \"http://localhost:28173\"\napi_key = \"secret\"\ntimezone = \"UTC\"\n", encoding="utf-8")
        assert kwargs["base_url"] == "http://localhost:28173"
        assert kwargs["username"] == "admin"
        assert kwargs["password"] == "admin"
        assert kwargs["api_key_name"] == "czm-cli"
        assert kwargs["config_path"] == config_path
        return type("R", (), {"config_path": config_path, "base_url": kwargs["base_url"], "username": kwargs["username"], "api_key_name": kwargs["api_key_name"], "timezone": kwargs["timezone"]})()

    monkeypatch.setattr(setup_module, "bootstrap_config", fake_bootstrap_config)
    exit_code = cli_module.main(["setup", "--config", str(config_path)])
    assert exit_code == 0
    assert "Wrote config to" in capsys.readouterr().out
    assert config_path.exists()


def test_cli_setup_command_custom_base_url(monkeypatch, tmp_path):
    from czm_cli.commands import setup as setup_module

    config_path = tmp_path / "config.toml"
    custom_base_url = "http://backend-host:28173"

    def fake_bootstrap_config(**kwargs):
        assert kwargs["base_url"] == custom_base_url
        assert kwargs["config_path"] == config_path
        return type(
            "R",
            (),
            {
                "config_path": config_path,
                "base_url": kwargs["base_url"],
                "username": kwargs["username"],
                "api_key_name": kwargs["api_key_name"],
                "timezone": kwargs["timezone"],
            },
        )()

    monkeypatch.setattr(setup_module, "bootstrap_config", fake_bootstrap_config)
    exit_code = cli_module.main(["setup", "--config", str(config_path), "--base-url", custom_base_url])
    assert exit_code == 0


def test_cli_uses_configured_base_url(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'base_url = "http://backend-host:28173"\napi_key = "secret"\ntimezone = "UTC"\n',
        encoding="utf-8",
    )
    seen = {}

    class RecordingClient(FakeClient):
        def __init__(self, base_url, api_key, **kwargs):
            seen["base_url"] = base_url
            seen["api_key"] = api_key
            super().__init__({("GET", "/subjects"): {"subjects": []}})

    monkeypatch.setattr(cli_module, "CzmClient", RecordingClient)
    exit_code = cli_module.main(["--config", str(config), "subject", "list"])
    assert exit_code == 0
    assert seen["base_url"] == "http://backend-host:28173"
    assert seen["api_key"] == "secret"


def test_cli_backup_export_writes_file(monkeypatch, tmp_path, capsys):
    backup_path = tmp_path / "backup.json"
    fake = FakeClient({("DOWNLOAD", "/export"): (b'{"format":"zema.account-export"}', "application/json")})
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(["--base-url", "http://example", "--api-key", "k", "backup", "export", "--output", str(backup_path)])

    assert exit_code == 0
    assert backup_path.read_bytes() == b'{"format":"zema.account-export"}'
    assert fake.requests == [("DOWNLOAD", "/export")]
    assert "Wrote backup to" in capsys.readouterr().out


def test_cli_backup_import_requires_confirmation(monkeypatch, tmp_path, capsys):
    backup_path = tmp_path / "backup.json"
    backup_path.write_text("{}", encoding="utf-8")
    fake = FakeClient({})
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(["--base-url", "http://example", "--api-key", "k", "backup", "import", str(backup_path)])

    assert exit_code == EXIT_USAGE
    assert "rerun with --yes" in capsys.readouterr().err


def test_cli_backup_import_uploads_json(monkeypatch, tmp_path, capsys):
    backup_path = tmp_path / "backup.json"
    backup_path.write_text("{}", encoding="utf-8")
    fake = FakeClient(
        {
            ("UPLOAD", "/import"): {
                "imported": {"subjects": 1, "locations": 2, "episodes": 3, "applications": 4, "events": 5, "adherence_days": 6}
            }
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(["--base-url", "http://example", "--api-key", "k", "backup", "import", str(backup_path), "--yes"])

    assert exit_code == 0
    assert fake.requests == [("UPLOAD", "/import", "file", str(backup_path), "application/json")]
    assert "Imported backup: 1 subjects, 2 locations, 3 episodes, 4 applications." in capsys.readouterr().out


def test_cli_setup_rejects_invalid_base_url(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    exit_code = cli_module.main(["setup", "--config", str(config_path), "--base-url", "not-a-url"])
    assert exit_code == EXIT_USAGE
    assert "base_url must be an http or https URL" in capsys.readouterr().err


def test_episode_relapse_uses_explicit_reason(monkeypatch):
    fake = FakeClient(
        {
            ("POST", "/episodes/1/relapse"): {
                "episode": {
                    "id": 1,
                    "subject_id": 1,
                    "location_id": 1,
                    "status": "active_flare",
                    "current_phase_number": 1,
                    "phase_started_at": "2026-04-15T18:00:00Z",
                    "phase_due_end_at": None,
                    "protocol_version": "v1",
                    "healed_at": None,
                    "obsolete_at": None,
                }
            }
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())
    exit_code = cli_module.main(
        [
            "--json",
            "--base-url",
            "http://example",
            "--api-key",
            "k",
            "episode",
            "relapse",
            "1",
            "--reason",
            "symptoms_returned",
        ]
    )
    assert exit_code == 0
    assert fake.requests[-1] == (
        "POST",
        "/episodes/1/relapse",
        {"reason": "symptoms_returned"},
    )


def test_episode_relapse_defaults_reason_when_omitted(monkeypatch):
    fake = FakeClient(
        {
            ("POST", "/episodes/1/relapse"): {
                "episode": {
                    "id": 1,
                    "subject_id": 1,
                    "location_id": 1,
                    "status": "active_flare",
                    "current_phase_number": 1,
                    "phase_started_at": "2026-04-15T18:00:00Z",
                    "phase_due_end_at": None,
                    "protocol_version": "v1",
                    "healed_at": None,
                    "obsolete_at": None,
                }
            }
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())
    exit_code = cli_module.main(
        [
            "--json",
            "--base-url",
            "http://example",
            "--api-key",
            "k",
            "episode",
            "relapse",
            "1",
            "--reported-at",
            "2026-04-15T21:00:00",
        ]
    )
    assert exit_code == 0
    assert fake.requests[-1] == (
        "POST",
        "/episodes/1/relapse",
        {"reason": "relapse", "reported_at": "2026-04-15T21:00:00Z"},
    )


def test_episode_relapse_help_text_marks_reason_optional(capsys):
    with pytest.raises(SystemExit):
        cli_module.main(["episode", "relapse", "--help"])
    out = capsys.readouterr().out
    assert "--reason" in out
    assert "Optional human-readable relapse reason" in out


def test_application_log_allows_minimal_payload(monkeypatch):
    fake = FakeClient(
        {
            ("POST", "/applications"): {
                "application": {
                    "id": 1,
                    "episode_id": 7,
                    "applied_at": "2026-04-15T20:30:00Z",
                    "treatment_type": "other",
                    "treatment_name": None,
                    "quantity_text": None,
                    "phase_number_snapshot": 1,
                    "is_voided": False,
                    "voided_at": None,
                    "is_deleted": False,
                    "deleted_at": None,
                    "notes": None,
                    "created_at": "2026-04-15T20:30:00Z",
                }
            }
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(
        [
            "--json",
            "--base-url",
            "http://example",
            "--api-key",
            "k",
            "application",
            "log",
            "--episode",
            "7",
        ]
    )

    assert exit_code == 0
    assert fake.requests[-1] == ("POST", "/applications", {"episode_id": 7})


def _location_payload(image: dict | None = None) -> dict:
    return {"location": {"id": 3, "code": "left_elbow", "display_name": "Left elbow", "image": image}}


def _image_payload() -> dict:
    return {
        "mime_type": "image/png",
        "size_bytes": 24,
        "sha256": "a" * 64,
        "original_filename": "left-elbow.png",
        "uploaded_at": "2026-04-25T10:00:00Z",
        "url": "/locations/3/image",
    }


def test_location_create_with_image_uploads_after_create(monkeypatch, tmp_path, capsys):
    image_path = tmp_path / "left-elbow.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    fake = FakeClient(
        {
            ("POST", "/locations"): _location_payload(),
            ("UPLOAD", "/locations/3/image"): _location_payload(_image_payload()),
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(
        [
            "--json",
            "--base-url",
            "http://example",
            "--api-key",
            "k",
            "location",
            "create",
            "--code",
            "left_elbow",
            "--display-name",
            "Left elbow",
            "--image",
            str(image_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["location"]["image"]["mime_type"] == "image/png"
    assert fake.requests == [
        ("POST", "/locations", {"code": "left_elbow", "display_name": "Left elbow"}),
        ("UPLOAD", "/locations/3/image", "image", str(image_path), "image/png"),
    ]


def test_location_image_set_resolves_and_uploads(monkeypatch, tmp_path):
    image_path = tmp_path / "left-elbow.webp"
    image_path.write_bytes(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 12)
    fake = FakeClient(
        {
            ("GET", "/locations"): {"locations": [{"id": 3, "code": "left_elbow", "display_name": "Left elbow"}]},
            ("UPLOAD", "/locations/3/image"): _location_payload(_image_payload()),
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(
        [
            "--base-url",
            "http://example",
            "--api-key",
            "k",
            "location",
            "image",
            "set",
            "left_elbow",
            str(image_path),
        ]
    )

    assert exit_code == 0
    assert fake.requests[-1] == ("UPLOAD", "/locations/3/image", "image", str(image_path), "image/webp")


def test_location_image_get_writes_output(monkeypatch, tmp_path, capsys):
    output_path = tmp_path / "downloaded.png"
    fake = FakeClient(
        {
            ("GET", "/locations"): {"locations": [{"id": 3, "code": "left_elbow", "display_name": "Left elbow"}]},
            ("DOWNLOAD", "/locations/3/image"): (b"image-bytes", "image/png"),
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(
        [
            "--base-url",
            "http://example",
            "--api-key",
            "k",
            "location",
            "image",
            "get",
            "left_elbow",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert output_path.read_bytes() == b"image-bytes"
    assert "Wrote location image" in capsys.readouterr().out


def test_location_image_get_rejects_json(monkeypatch, tmp_path):
    fake = FakeClient({})
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(
        [
            "--json",
            "location",
            "image",
            "get",
            "left_elbow",
            "--output",
            str(tmp_path / "downloaded.png"),
        ]
    )

    assert exit_code == EXIT_USAGE


def test_location_image_remove_resolves_and_deletes(monkeypatch, capsys):
    fake = FakeClient(
        {
            ("GET", "/locations"): {"locations": [{"id": 3, "code": "left_elbow", "display_name": "Left elbow"}]},
            ("DELETE", "/locations/3/image"): _location_payload(None),
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(
        [
            "--json",
            "--base-url",
            "http://example",
            "--api-key",
            "k",
            "location",
            "image",
            "remove",
            "left_elbow",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["location"]["image"] is None
    assert fake.requests[-1] == ("DELETE", "/locations/3/image", None)


def test_location_image_set_rejects_missing_file(monkeypatch):
    fake = FakeClient(
        {
            ("GET", "/locations"): {"locations": [{"id": 3, "code": "left_elbow", "display_name": "Left elbow"}]},
        }
    )
    monkeypatch.setattr(cli_module, "CzmClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli_module, "resolve_runtime_config", lambda **kwargs: DummyConfig())

    exit_code = cli_module.main(
        [
            "location",
            "image",
            "set",
            "left_elbow",
            "/does/not/exist.png",
        ]
    )

    assert exit_code == EXIT_USAGE
