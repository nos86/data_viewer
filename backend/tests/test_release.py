"""Pipeline files and release checks (RI-43): the YAML parses and refers to files that exist; tag rules; wheel check."""
import importlib.util
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = load("release_check")


@pytest.mark.parametrize("tag", ["v1.0.0", "v0.12.3"])
def test_valid_tags(tag):
    rc.check_tag(tag, ["v0.1.0", "junk"])


@pytest.mark.parametrize("tag", ["1.0.0", "v1.0", "v1.0.0-rc1", "release-3"])
def test_malformed_tags_are_rejected(tag):
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        rc.check_tag(tag, [])


def test_tag_must_be_newer_than_the_others():
    with pytest.raises(ValueError, match="not newer"):
        rc.check_tag("v1.2.0", ["v1.10.0"])        # numeric, not alphabetic comparison
    with pytest.raises(ValueError, match="not newer"):
        rc.check_tag("v1.2.0", ["v1.2.0", "v1.2.1"])
    rc.check_tag("v1.10.0", ["v1.9.9", "v1.2.0"])


def test_non_tag_refs_pass():
    assert rc.main(["--ref", "refs/heads/main"]) == 0


def test_wheel_check(tmp_path):
    good, bad = tmp_path / "good.whl", tmp_path / "bad.whl"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("viewer/static/index.html", "x")
        z.writestr("viewer-1.dist-info/entry_points.txt", "[console_scripts]\nviewer = viewer.cli:main\n")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("viewer/__init__.py", "")
    rc.check_wheel(good)
    with pytest.raises(ValueError, match="index.html"):
        rc.check_wheel(bad)


@pytest.mark.parametrize("name", ["azure-pipelines.yml", "azure-pipelines-deploy.yml", "databricks.yml", "app.yaml"])
def test_yaml_files_parse(name):
    assert isinstance(yaml.safe_load((ROOT / name).read_text(encoding="utf8")), dict)


def test_pipeline_stages_and_references():
    p = yaml.safe_load((ROOT / "azure-pipelines.yml").read_text(encoding="utf8"))
    assert [s["stage"] for s in p["stages"]] == ["Test", "Build", "Staging", "Production"]
    assert "v*.*.*" in p["trigger"]["tags"]["include"]
    text = (ROOT / "azure-pipelines.yml").read_text(encoding="utf8") + (ROOT / "azure-pipelines-deploy.yml").read_text(encoding="utf8")
    for ref in ["scripts/release_check.py", "scripts/build_frontend.py", "scripts/databricks_smoke.py", "azure-pipelines-deploy.yml"]:
        assert ref in text and (ROOT / ref).is_file(), ref
    stages = {s["stage"]: s for s in p["stages"]}
    assert stages["Staging"]["jobs"][0]["environment"] == "viewer-staging" and stages["Production"]["jobs"][0]["environment"] == "viewer-production"
    assert "isRelease" in stages["Production"]["condition"]


def test_app_definition_matches_the_settings():
    from viewer.config import Settings

    app = yaml.safe_load((ROOT / "app.yaml").read_text(encoding="utf8"))
    names = {e["name"] for e in app["env"]}
    fields = {"VIEWER_" + f.upper() for f in Settings.__dataclass_fields__}
    assert names <= fields, names - fields
    assert app["command"][:2] == ["viewer", "serve"]
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf8"))
    assert set(bundle["targets"]) == {"staging", "production"} and "viewer_app" in bundle["resources"]["apps"]
