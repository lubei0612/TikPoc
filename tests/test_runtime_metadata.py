from tikpoc.runtime_metadata import runtime_metadata


def test_runtime_metadata_reports_auditable_versions_without_environment_dump(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TIKPOC_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("TIKPOC_MOBILE_BOOTSTRAP_TOKEN", "secret-value")

    metadata = runtime_metadata()

    assert metadata["build_commit"] == "abc1234"
    assert metadata["policy_version"]
    assert metadata["device_protocol_version"] == 1
    assert metadata["supported_helper_versions"] == ["1.0.0"]
    assert "secret-value" not in repr(metadata)
