import json
import re
import xml.etree.ElementTree as element_tree
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_local_mcp_example_is_valid_json() -> None:
    path = REPOSITORY_ROOT / "examples" / "mcp.local.example.json"

    value = json.loads(path.read_text(encoding="utf-8"))

    server = value["mcpServers"]["cadplot"]
    assert server["command"] == "uv"
    assert "CADPLOT_CONFIG" in server["env"]


def test_repository_markdown_local_links_exist() -> None:
    documents = list(REPOSITORY_ROOT.glob("*.md"))
    for folder in ("docs", "bundle", "integrations"):
        documents.extend((REPOSITORY_ROOT / folder).rglob("*.md"))

    missing: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            path_part = target.split("#", 1)[0]
            if path_part and not (document.parent / path_part).resolve().exists():
                relative_document = document.relative_to(REPOSITORY_ROOT)
                missing.append(f"{relative_document}: {target}")

    assert missing == []


def test_bundle_routes_supported_autocad_series() -> None:
    path = REPOSITORY_ROOT / "bundle" / "CadPlotMcp.bundle" / "PackageContents.xml"
    root = element_tree.parse(path).getroot()
    routes = {
        (
            item.attrib["SeriesMin"],
            item.attrib["SeriesMax"],
        )
        for item in root.findall("./Components/ComponentEntry/RuntimeRequirements")
    }

    assert routes == {("R20.1", "R20.1"), ("R25.0", "R25.1")}


def test_repository_contains_mit_license() -> None:
    license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "MIT License" in license_text
    assert "Demir Eren" in license_text


def test_version_adapters_require_all_managed_autocad_references() -> None:
    for release in ("2016", "2025"):
        project = (
            REPOSITORY_ROOT
            / "src"
            / "dotnet"
            / f"CadPlotMcp.AutoCAD{release}"
            / (f"CadPlotMcp.AutoCAD{release}.csproj")
        )
        text = project.read_text(encoding="utf-8")

        assert "AcMgd.dll" in text
        assert "AcDbMgd.dll" in text
        assert "AcCoreMgd.dll" in text
        assert "AutoCadPublishRuntime.cs" in text


def test_api_probe_is_compile_only() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "probe-autocad-api.ps1").read_text(encoding="utf-8")

    assert "dotnet" in script.casefold()
    assert "AutoCAD was not launched" in script
    assert "Start-Process" not in script


def test_build_and_install_require_exact_bundle_verification() -> None:
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-bundle.ps1").read_text(encoding="utf-8")
    for required in (
        "PackageContents.xml",
        "CadPlotMcp.AutoCAD2016.dll",
        "CadPlotMcp.AutoCAD2025.dll",
        "CadPlotMcp.Core.dll",
        "GetAssemblyName",
        "Get-FileHash",
        "ReparsePoint",
    ):
        assert required in verifier

    for script_name in ("build-bundle.ps1", "install-bundle.ps1"):
        script = (REPOSITORY_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert "verify-bundle.ps1" in script


def test_uninstaller_is_identity_gated_and_supports_what_if() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "uninstall-bundle.ps1").read_text(encoding="utf-8")

    assert "SupportsShouldProcess = $true" in script
    assert "C2E79B66-6076-40D4-AE45-E725A644B288" in script
    assert "ReparsePoint" in script
    assert "ShouldProcess($destinationBundle" in script
    assert "Remove-Item -LiteralPath $destinationBundle" in script


def test_codex_plugin_manifest_routes_installed_cadplot_cli() -> None:
    plugin_root = REPOSITORY_ROOT / "integrations" / "codex" / "cadplot-mcp"
    manifest = json.loads(
        (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    mcp = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "cadplot-mcp"
    assert manifest["license"] == "MIT"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "Write" in manifest["interface"]["capabilities"]
    assert mcp["mcpServers"]["cadplot"] == {"command": "cadplot-mcp", "args": []}


def test_deployment_docs_separate_local_and_remote_boundaries() -> None:
    deployment = (REPOSITORY_ROOT / "docs" / "deployment-modes.md").read_text(encoding="utf-8")
    architecture = (REPOSITORY_ROOT / "docs" / "chatgpt-connection.md").read_text(encoding="utf-8")

    assert "Local workstation" in deployment
    assert "ChatGPT web developer pilot" in deployment
    assert "Managed company deployment" in deployment
    assert "Public ChatGPT plugin" in deployment
    assert "Bridge not implemented" in deployment
    assert "named pipe" in deployment
    assert "publish_verified=true" in deployment
    assert "The DWG stays" in architecture
    assert "Not implemented or claimed" in architecture
