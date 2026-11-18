#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""تفعيل الوضع الآلي بالكامل: يضبط config.ini على ملف الإعداد الآلي.

يستبدل التحرير اليدوي لـ config.ini بملف واحد يُطبَّق بضغطة واحدة على جهاز
المشغل (Windows أو غيره):

- RIGHTS_GATE_MODE = operator_confirmed مع بيان وإثبات من المشغل نفسه.
- REVIEW_BEFORE_UPLOAD = false (بلا موافقة لكل فيديو).
- PRESERVE_FACEBOOK_TITLE = false + TITLE_OPTIMIZE = true (عناوين ذكية).
- [AI] ENABLED = true (لا يعمل بلا مفتاح في ai_keys.json).

الإقرار المُدخل يجب أن يكون صادقاً؛ الأداة ترفض بياناً أقصر من 25 حرفاً وأساساً
(licensed/permission/public_domain) بلا مرجع إثبات، بنفس قواعد content_safety.
الاستخدام:

  python enable_automation.py --show
  python enable_automation.py --dry-run --basis owned --statement "..."
  python enable_automation.py --basis owned --statement "..." --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config_store import read_config, write_config_atomically

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.ini"

RIGHTS_GATE_AUTO = "operator_confirmed"
STUDIO_KEYS = {
    "RIGHTS_GATE_MODE": RIGHTS_GATE_AUTO,
    "REVIEW_BEFORE_UPLOAD": "false",
    "PRESERVE_FACEBOOK_TITLE": "false",
    "TITLE_OPTIMIZE": "true",
}
AI_KEYS = {"ENABLED": "true"}
BASIS_VALUES = ("owned", "licensed", "permission", "public_domain")
EVIDENCE_REQUIRED = {"licensed", "permission", "public_domain"}


def validate_inputs(basis: str, statement: str, evidence: str) -> None:
    basis = (basis or "").strip()
    if basis not in BASIS_VALUES:
        raise SystemExit(f"أساس حقوق غير معروف: {basis!r}؛ القيم المقبولة: {', '.join(BASIS_VALUES)}")
    if len((statement or "").strip()) < 25:
        raise SystemExit(
            "الإقرار قصير (أقل من 25 حرفاً)؛ اكتب بياناً واضحاً يفصّل ملكيتك للحق أو الإذن."
        )
    if basis in EVIDENCE_REQUIRED and len((evidence or "").strip()) < 5:
        raise SystemExit(
            f"الأساس {basis} يتطلب مرجع إثبات لا يقل عن 5 أحرف في --evidence؛ "
            "وإن كان الفيديو أصلياً وتملكه فاختر owned."
        )


def automation_profile(basis: str, statement: str, evidence: str) -> dict[str, dict[str, str]]:
    values = dict(STUDIO_KEYS)
    values["RIGHTS_OPERATOR_BASIS"] = (basis or "").strip()
    values["RIGHTS_OPERATOR_STATEMENT"] = (statement or "").strip()
    values["RIGHTS_OPERATOR_EVIDENCE"] = (evidence or "").strip()
    return {"STUDIO": values, "AI": dict(AI_KEYS)}


def apply_automation_profile(
    config_path: str | Path,
    basis: str,
    statement: str,
    evidence: str,
    *,
    dry_run: bool = False,
) -> dict[str, dict[str, tuple[str, str]]]:
    """يطبّق ملف الإعداد الآلي على config ويعيد التغييرات {القسم: {المفتاح: (قديم, جديد)}}."""
    validate_inputs(basis, statement, evidence)
    parser, _backup = read_config(config_path)
    changes: dict[str, dict[str, tuple[str, str]]] = {}
    for section, keys in automation_profile(basis, statement, evidence).items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in keys.items():
            old = parser.get(section, key, fallback="").strip()
            if old == value:
                continue
            changes.setdefault(section, {})[key] = (old, value)
            parser.set(section, key, value)
    if changes and not dry_run:
        write_config_atomically(parser, config_path)
    return changes


def describe_changes(changes: dict[str, dict[str, tuple[str, str]]]) -> str:
    if not changes:
        return "config.ini مضبوط بالفعل على الوضع الآلي؛ لا تغييرات."
    lines = []
    for section, keys in changes.items():
        for key, (old, new) in keys.items():
            shown_old = old if old else "(فارغ)"
            lines.append(f"[{section}] {key}: {shown_old} -> {new}")
    return "\n".join(lines)


def show_status(config_path: str | Path) -> str:
    parser, _backup = read_config(config_path)
    keys = [
        "RIGHTS_GATE_MODE",
        "RIGHTS_OPERATOR_BASIS",
        "RIGHTS_OPERATOR_STATEMENT",
        "RIGHTS_OPERATOR_EVIDENCE",
        "REVIEW_BEFORE_UPLOAD",
        "PRESERVE_FACEBOOK_TITLE",
        "TITLE_OPTIMIZE",
    ]
    lines = ["الإعدادات الحالية ذات الصلة:"]
    for key in keys:
        lines.append(f"[STUDIO] {key} = {parser.get('STUDIO', key, fallback='(غير مضبوط)').strip()}")
    lines.append(f"[AI] ENABLED = {parser.get('AI', 'ENABLED', fallback='(غير مضبوط)').strip()}")
    # حالة الملفات السرية تخص مجلد ملف الإعدادات نفسه، لا مجلد السكربت؛
    # مع --config في مجلد آخر كان العرض يبلّغ عن ملفات APP_DIR الخطأ.
    config_dir = Path(config_path).expanduser().resolve().parent
    ai_keys = config_dir / "ai_keys.json"
    secrets = config_dir / "client_secrets.json"
    tokens = sorted(config_dir.glob("*_token.json"))
    lines.append(f"ai_keys.json: {'موجود' if ai_keys.is_file() else 'مفقود — أضف مفتاح Gemini من الواجهة'}")
    lines.append(f"client_secrets.json: {'موجود' if secrets.is_file() else 'مفقود — لا يمكن تسجيل الدخول إلى YouTube بدونه'}")
    lines.append(f"رموز OAuth: {', '.join(p.name for p in tokens) if tokens else 'لا يوجد — سجّل الدخول مرة واحدة من الواجهة'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argument_parser = argparse.ArgumentParser(description="تفعيل الوضع الآلي الكامل في config.ini")
    argument_parser.add_argument("--config", default=str(CONFIG_PATH), help="مسار config.ini")
    argument_parser.add_argument("--basis", default="owned", choices=BASIS_VALUES, help="أساس الحقوق")
    argument_parser.add_argument("--statement", default="", help="إقرار المشغل الصادق (25 حرفاً على الأقل)")
    argument_parser.add_argument("--evidence", default="", help="مرجع الإثبات للأسس غير owned")
    argument_parser.add_argument("--dry-run", action="store_true", help="اعرض التغييرات دون كتابة")
    argument_parser.add_argument("--yes", action="store_true", help="تطبيق دون سؤال تفاعلي")
    argument_parser.add_argument("--show", action="store_true", help="عرض الإعدادات الحالية فقط")
    args = argument_parser.parse_args(argv)

    if args.show:
        print(show_status(args.config))
        return 0

    if not args.statement.strip():
        print("لا بد من --statement مع إقرار صادق وواضح؛ لا تُدخل إقراراً غير صحيح.", file=sys.stderr)
        return 2
    validate_inputs(args.basis, args.statement, args.evidence)
    changes = apply_automation_profile(args.config, args.basis, args.statement, args.evidence, dry_run=True)
    print("التغييرات التي ستُطبَّق:")
    print(describe_changes(changes))
    if args.dry_run:
        return 0
    if not changes:
        return 0
    if not args.yes:
        answer = input("\nهل تطبّق هذه التغييرات؟ اكتب نعم: ").strip()
        if answer not in {"نعم", "y", "Y", "yes"}:
            print("أُلغي التطبيق؛ لم يُعدَّل شيء.")
            return 1
    apply_automation_profile(args.config, args.basis, args.statement, args.evidence)
    print("\nتم تطبيق الوضع الآلي. ما يلزمك أيضاً:")
    print("- مفتاح Gemini في ai_keys.json (من الواجهة) وإلا ستُستخدم العناوين كما هي.")
    print("- تسجيل دخول YouTube مرة واحدة (client_secrets.json + رمز الحساب).")
    print("- الإقرار المُدخل يبقى مسؤوليتك؛ لا ترفع محتوى لا تملكه ولا تملك إذناً به.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
