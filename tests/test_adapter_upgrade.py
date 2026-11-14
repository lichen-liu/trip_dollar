"""Real pip ownership handoff for upgrades from the embedded adapter."""

from pathlib import Path


def test_upgrade_removes_only_the_legacy_app_owner(tmp_path):
    import importlib.util
    import subprocess
    import venv

    spec = importlib.util.spec_from_file_location(
        "adapter_upgrade_installer",
        Path(__file__).resolve().parents[1] / "installation/install.py",
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    environment = tmp_path / "environment"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / "bin/python"
    purelib = Path(
        subprocess.check_output(
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            text=True,
        ).strip()
    )
    package = purelib / "bobowatch"
    package.mkdir()
    (package / "legacy.py").write_text("legacy = True\n")
    info = purelib / "trip_dollar-0.0.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: trip-dollar\nVersion: 0.0.0\n"
    )
    (info / "RECORD").write_text(
        "bobowatch/legacy.py,,\ntrip_dollar-0.0.0.dist-info/METADATA,,\ntrip_dollar-0.0.0.dist-info/RECORD,,\n"
    )
    settings = tmp_path / "settings.json"
    settings.write_text("private state stays here\n")
    assert installer.remove_embedded_adapter(python)
    assert not (package / "legacy.py").exists()
    assert not info.exists()
    assert settings.read_text() == "private state stays here\n"
    assert not installer.remove_embedded_adapter(python)
