from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PipelineFailure(Exception):
    stage: str
    code: str
    cause: str
    action: str
    retryable: bool = False

    def __str__(self) -> str:
        cause = str(self.cause or "سبب غير معروف").strip().rstrip(".،; ")
        action = str(self.action or "راجع إعدادات التطبيق").strip().rstrip(".،; ")
        retry_note = " يمكن إعادة المحاولة بعد معالجة السبب." if self.retryable else " لن تبدأ إعادة تلقائية غير آمنة."
        return (
            f"توقفت المرحلة «{self.stage}». السبب: {cause}. "
            f"الإجراء الآمن التالي: {action}.{retry_note}"
        )
