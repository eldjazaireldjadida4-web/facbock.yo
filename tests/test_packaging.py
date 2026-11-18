"""عقد التغليف — الحزمة والملفات لا تشحن أسراراً أبداً، والتثبيت للمستخدم
لا لـ Program Files، وسير عمل الإصدار يبني مثبّت Windows.

هذه اختبارات ثابتة (تقرأ الملفات ولا تبني شيئاً): البناء الفعلي والتحقق
من الحزمة موثّقان في docs/packaging.md.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import version

ROOT = Path(__file__).resolve().parents[1]

SPEC = ROOT / "facbock-yo.spec"
ISS = ROOT / "installer" / "facbock-yo.iss"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

SECRET_BASENAMES = {"config.ini", "client_secrets.json", "ai_keys.json"}
SECRET_SUFFIXES = ("_token.json",)


def _is_secret_name(name: str) -> bool:
    return name in SECRET_BASENAMES or name.endswith(SECRET_SUFFIXES)


def _spec_datas_entries() -> list[str]:
    """أسماء الملفات في القائمة الحرفية DATAS داخل ملف spec."""
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DATAS" for target in node.targets
        ):
            names = []
            for child in ast.walk(node.value):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    name = Path(child.value).name
                    if name not in (".", ""):
                        names.append(name)
            return names
    raise AssertionError("DATAS list not found in facbock-yo.spec")


def test_spec_targets_main_entry_and_onedir_console() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "fb_youtube_uploader_v49_pro.py" in text
    assert "doctor.py" in text
    assert "enable_automation.py" in text
    assert "console=True" in text
    assert 'name="facbock-yo"' in text
    assert "COLLECT(" in text


def test_spec_excludes_transcription_stack() -> None:
    text = SPEC.read_text(encoding="utf-8")
    for module in (
        "faster_whisper",
        "onnxruntime",
        "ctranslate2",
        "tokenizers",
        "huggingface_hub",
        "av",
    ):
        assert f'"{module}"' in text, f"{module} must stay excluded from the bundle"


def test_spec_bundles_examples_never_secrets() -> None:
    entries = _spec_datas_entries()
    assert "config.ini.example" in entries
    assert "rights_manifest.example.json" in entries
    for entry in entries:
        assert not _is_secret_name(entry), f"secret file must never be bundled: {entry}"


def test_spec_doctor_hidden_imports_cover_runtime_imports() -> None:
    """The frozen doctor must import every module its library check verifies."""
    from dependency_check import RUNTIME_IMPORTS

    text = SPEC.read_text(encoding="utf-8")
    for module, _package in RUNTIME_IMPORTS:
        assert f'"{module}"' in text, f"doctor bundle must import {module}"


def test_spec_reads_version_from_version_py() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "version.py" in text
    assert "APP_VERSION" in text


def test_inno_setup_is_per_user_not_program_files() -> None:
    text = ISS.read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in text
    install_dir = next(
        line for line in text.splitlines() if line.startswith("DefaultDirName=")
    )
    assert "{localappdata}" in install_dir
    assert "{pf}" not in install_dir
    assert "Program Files" not in install_dir


def test_inno_setup_never_ships_secrets() -> None:
    text = ISS.read_text(encoding="utf-8")
    files_section = text.split("[Files]", 1)[1].split("[", 1)[0]
    for line in files_section.splitlines():
        line = line.strip()
        if not line.startswith("Source:"):
            continue
        source = line.split("Source:", 1)[1].split(";", 1)[0].strip().strip('"')
        # The .iss uses Windows separators; normalise before taking the name.
        name = Path(source.replace("\\", "/")).name
        if name.endswith(".example") or name.endswith(".example.json"):
            continue
        assert not _is_secret_name(name), f"secret file must never ship: {line}"
        assert name not in {"config.ini", "rights_manifest.json"} or "onlyifdoesntexist" in line, (
            f"user config must only be created when missing: {line}"
        )
    # The only config.ini the installer ever creates comes from the example.
    assert 'DestName: "config.ini"' in text
    assert "onlyifdoesntexist" in text


def test_inno_setup_tasks_flags_are_valid() -> None:
    """[Tasks] Flags must be real Inno Setup flags.

    There is no "checked" flag: a task is checked by default, and the
    opt-out is "unchecked". ISCC 6.7.1 aborted the v4.49.29 installer
    build on `Flags: checked` ("Parameter Flags includes an unknown
    flag"), so pin the documented set here.
    """
    valid = {"checkablealone", "checkedonce", "dontinheritcheck", "exclusive", "restart", "unchecked"}
    text = ISS.read_text(encoding="utf-8")
    tasks_section = text.split("[Tasks]", 1)[1].split("[", 1)[0]
    flags_seen = []
    for line in tasks_section.splitlines():
        line = line.strip()
        if "Flags:" not in line:
            continue
        flags = line.split("Flags:", 1)[1].strip().split()
        flags_seen.extend(flags)
        for flag in flags:
            assert flag in valid, f"unknown [Tasks] flag in installer/facbock-yo.iss: {flag}"
    assert flags_seen or True  # a [Tasks] entry without Flags is fine (checked by default)


def test_inno_setup_version_default_matches_version_py() -> None:
    text = ISS.read_text(encoding="utf-8")
    match = re.search(r'#define AppVersion "([^"]+)"', text)
    assert match, "AppVersion default not found in the .iss file"
    assert match.group(1) == version.APP_VERSION


def test_release_workflow_builds_windows_installer() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in text
    assert "PyInstaller" in text
    assert "ISCC" in text
    assert "softprops/action-gh-release" in text
    assert "contents: write" in text
    # --preflight exits 2 in a clean checkout (missing credentials): the
    # workflow must expect that, not treat it as a crash.
    assert "expected 2" in text


def test_release_workflow_copies_examples_to_bundle_root() -> None:
    """The installer sources the examples from the bundle root.

    PyInstaller 6 hides data files under _internal, so after the build
    the examples only exist at dist\\facbock-yo\\_internal\\. Both build
    scripts copy them to the bundle root, and the release workflow must
    do the same — without the copy ISCC aborts the installer build with
    'Source file ...\\config.ini.example does not exist'.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "Copy-Item config.ini.example" in text
    assert "Copy-Item rights_manifest.example.json" in text
    assert "dist\\facbock-yo\\config.ini.example" in text
    assert "dist\\facbock-yo\\rights_manifest.example.json" in text


def test_build_scripts_exist_and_use_lockfile() -> None:
    for name in ("build_windows.bat", "build_linux.sh"):
        path = ROOT / name
        assert path.is_file(), f"missing build script: {name}"
        text = path.read_text(encoding="utf-8")
        assert "PyInstaller" in text
        assert "requirements-lock.txt" in text
        assert "facbock-yo.spec" in text
        # PyInstaller 6 hides data files under _internal; the build scripts
        # must copy the example configs to the bundle root, where the
        # installer/launcher (and the user) look for them.
        assert "config.ini.example" in text
