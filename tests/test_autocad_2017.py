import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from test_autocad_com import FakeComClient
from test_client_config import _client_config
from test_doctor import _config

from cadplot_mcp.backends.autocad_com import AutoCADComInspector, AutoCADUnavailableError
from cadplot_mcp.client_config import probe_client_config
from cadplot_mcp.environment import diagnose_environment

ROOT = Path(__file__).resolve().parents[1]


def test_2017_com_attaches_only_to_exact_registered_release():
    inspector = AutoCADComInspector(object(), progid="AutoCAD.Application.21.0")
    client = FakeComClient("21.0s (LMS Tech)")
    assert inspector._get_active_application(client).Version == "21.0s (LMS Tech)"
    assert client.requested == "AutoCAD.Application.21.0"
    for version in ("20.1s (LMS Tech)", "22.0s (LMS Tech)", "21.01", "25.0"):
        with pytest.raises(AutoCADUnavailableError, match="identity mismatch"):
            inspector._get_active_application(FakeComClient(version))


@pytest.mark.parametrize("series,ready", [("R21.0", True), ("R20.1", False), ("R25.0", False)])
def test_2017_doctor_checks_com_against_plugin(monkeypatch, tmp_path, series, ready):
    config = _config(tmp_path)
    monkeypatch.setenv("CADPLOT_AUTOCAD_PROGID", "AutoCAD.Application.21.0")
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _: {"available": True, "progid": "AutoCAD.Application.21.0", "version": "21.0"},
    )
    monkeypatch.setattr(
        "cadplot_mcp.environment.get_plugin_status",
        lambda **_: {
            "ok": True,
            "readOnly": True,
            "workspaceConfigured": True,
            "runtimeSupported": True,
            "runtimeSeries": series,
            "adapter": "autocad-2017-net46",
            "publishEnabled": True,
            "queueAuthentication": "windows-dpapi-current-user+hmac-sha256-v1",
        },
    )
    report = diagnose_environment(config, mode="full", expect_publish_enabled=True)
    assert report["ready"] is ready
    assert report["plugin"]["inspection_identity_matched"] is ready


@pytest.mark.parametrize("mode", ["readonly", "publish"])
def test_2017_client_configuration_can_initialize_mcp(tmp_path, mode):
    path, old_id = _client_config(tmp_path, mode=mode)
    raw = json.loads(path.read_text(encoding="utf-8"))
    server = raw["mcpServers"].pop(old_id)
    new_id = f"cadplot-2017-{mode}"
    server["env"]["CADPLOT_AUTOCAD_PROGID"] = "AutoCAD.Application.21.0"
    server["env"]["CADPLOT_PIPE_NAME"] = "cadplot-mcp-2017"
    raw["mcpServers"][new_id] = server
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = probe_client_config(path, server_id=new_id, expected_mode=mode)
    assert result["passed"] is True
    assert result["tool_count"] == 20


def test_2017_native_target_loader_and_package_version_match():
    project = ET.parse(ROOT / "src/dotnet/CadPlotMcp.AutoCAD2017/CadPlotMcp.AutoCAD2017.csproj")
    assert project.findtext("./PropertyGroup/TargetFramework") == "net46"
    refs = project.findall("./ItemGroup/Reference")
    assert {r.attrib["Include"] for r in refs} == {"AcMgd", "AcDbMgd", "AcCoreMgd"}
    assert all(r.findtext("Private") == "false" for r in refs)
    manifest = ET.parse(ROOT / "bundle/CadPlotMcp2017.bundle/PackageContents.xml")
    entries = manifest.findall("./Components/ComponentEntry")
    assert len(entries) == 1
    assert entries[0].attrib["ModuleName"].endswith("/2017/CadPlotMcp.AutoCAD2017.dll")
    requirement = entries[0].find("RuntimeRequirements")
    assert requirement.attrib["SeriesMin"] == requirement.attrib["SeriesMax"] == "R21.0"
    import tomllib

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest.getroot().attrib["AppVersion"] == metadata["project"]["version"]
    legacy = ET.parse(ROOT / "bundle/CadPlotMcp.bundle/PackageContents.xml")
    assert legacy.getroot().attrib["AppVersion"] == metadata["project"]["version"]


def test_2017_builder_never_uses_old_adapter_or_downloads_sdk():
    source = (ROOT / "scripts/build-bundle-2017.ps1").read_text(encoding="utf-8")
    for required in (
        "find-autocad-2017.ps1",
        'ExpectedSeries "R21.0"',
        "AutoCAD2017SdkDir",
        "--no-incremental",
        "matching_sdk_bundle_built",
    ):
        assert required in source
    assert "AutoCAD2016" not in source and "AutoCAD2025" not in source
    finder = (ROOT / "scripts/find-autocad-2017.ps1").read_text(encoding="utf-8")
    assert "Invoke-WebRequest" not in finder
    assert '-ExpectedSeries "R21.0"' in finder
