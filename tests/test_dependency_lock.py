"""قفل الاعتماديات ومصفوفة CI — دعم Python 3.11 (4.49.22).

`av` و`numpy` في ملف القفل مشروطان بعلامة إصدار Python: الإصدارات
الأحدث تتطلب 3.12+، فتثبيت القفل بلا علامات على 3.11 يفشل عند التثبيت
قبل أي اختبار. هذه الاختبارات تثبّت العلامات ووجود 3.11 في CI.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _lock_pins():
    pins = []
    for line in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)(?:;\s*(.*))?$", line)
        assert match, f"unparsable lock line: {line!r}"
        pins.append((match.group(1).lower(), match.group(2), match.group(3) or ""))
    return pins


def test_lock_has_py311_variants_for_av_and_numpy():
    """`av` و`numpy` يجب أن يكون لكل منهما متغير <3.12 ومتغير >=3.12."""
    pins = _lock_pins()
    for package, old_version, new_version in (
        ("av", "18.1.0", "19.0.2"),
        ("numpy", "2.4.6", "2.5.5"),
    ):
        variants = {version: marker for name, version, marker in pins if name == package}
        assert variants.get(old_version) == 'python_version < "3.12"', variants
        assert variants.get(new_version) == 'python_version >= "3.12"', variants


def test_ci_matrix_includes_python_311():
    """مصفوفة CI يجب أن تشمل 3.11 بعد التحقق الفعلي تحت 3.11.18."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    python_versions = workflow["jobs"]["test"]["strategy"]["matrix"]["python"]
    assert "3.11" in [str(v) for v in python_versions]
    assert "3.12" in [str(v) for v in python_versions]
    assert "3.14" in [str(v) for v in python_versions]
