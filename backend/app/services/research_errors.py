"""Allowlisted, content-free diagnostics; never serialize exception messages/inputs."""
import logging

from pydantic import ValidationError

from app.models import utcnow

ERRORS = {
    "OUTPUT_JSON_INVALID": ("parsing", "模型返回的内容不是有效 JSON，未保存为研究结论。"),
    "OUTPUT_SCHEMA_INVALID": ("structure", "模型输出不符合研究结构，请查看具体校验项。"),
    "FINDING_KIND_INVALID": ("structure", "原文发现与综合判断的类型不符合约定。"),
    "EVIDENCE_ID_UNKNOWN": ("citation", "研究结论引用了不存在的证据。"),
    "EVIDENCE_ID_DUPLICATE": ("citation", "同一研究结论重复绑定了证据。"),
    "STEP_ID_UNKNOWN": ("binding", "研究结论关联了不存在的子问题。"),
    "STEP_EVIDENCE_MISMATCH": ("binding", "引用证据未绑定到该研究子问题。"),
    "QUOTE_NOT_VERBATIM": ("attribution", "原文发现不是所绑定证据的连续摘录。"),
    "CONFLICT_UNRESOLVED": ("evidence", "存在未解决的来源冲突，不能据此生成综合判断。"),
    "EVIDENCE_GAP": ("evidence", "该子问题仍有证据缺口，不能据此生成综合判断。"),
    "CITATION_INVALID": ("citation", "研究结论的引用检查未通过。"),
    "CITATION_MISSING": ("citation", "研究结论存在没有引用的内容。"),
    "NUMERIC_MISMATCH": ("citation", "研究结论的数值与引用证据不一致。"),
    "COVERAGE_INCOMPLETE": ("coverage", "研究发现未覆盖所有已有证据的子问题。"),
    "BUDGET_MODEL_CALLS": ("budget", "研究模型调用次数已达上限。"),
    "BUDGET_TOKENS": ("budget", "研究预留 Token 已达上限。"),
    "BUDGET_CONTEXT": ("budget", "本次研究输入超过配置的上下文容量。"),
    "RETRIEVAL_LIMIT": ("budget", "研究检索轮次已达上限。"),
    "MODEL_REQUEST_FAILED": ("provider", "模型请求失败，已有证据已保留。"),
    "MODEL_REQUEST_BLOCKED": ("policy", "模型调用被本地策略阻止，不能据此认定供应商故障。"),
    "MODEL_CONFIGURATION_FAILED": ("configuration", "模型适配器配置失败，尚未进入调用执行。"),
    "MODEL_ADAPTER_FAILED": ("adapter", "模型适配器或本地传输处理失败；是否已外发需结合传输记录核对。"),
    "MODEL_HTTP_FAILED": ("provider", "模型接口返回HTTP错误，已有证据已保留。"),
    "MODEL_AUTH_FAILED": ("provider", "模型接口拒绝认证或访问，已有证据已保留。"),
    "MODEL_RATE_LIMITED": ("provider", "模型接口返回限流，未自动重试。"),
    "MODEL_CONNECTION_FAILED": ("transport", "模型连接失败；是否到达供应商需结合传输记录核对。"),
    "MODEL_TIMEOUT": ("provider", "模型请求超时，已有证据已保留。"),
    "RESEARCH_TIMEOUT": ("timeout", "研究超时或服务中断，已有执行记录已保留。"),
    "ACCESS_REVOKED": ("access", "资料权限已变更，研究已停止。"),
    "RATE_LIMITED": ("budget", "研究调用频率达到限制。"),
    "RETRIEVAL_FAILED": ("retrieval", "资料检索失败，已有执行记录已保留。"),
    "PERSISTENCE_FAILED": ("persistence", "研究结果保存失败，已有执行记录已保留。"),
    "EXECUTION_FAILED": ("execution", "研究执行失败，已有执行记录已保留。"),
}
FIELDS = {"objective", "steps", "id", "question", "expected_output", "findings", "analysis", "kind", "text",
          "evidence_ids", "step_ids", "limitations", "follow_up_questions"}
RULES = {"schema", "kind", "evidence_exists", "evidence_unique", "step_exists", "step_binding", "verbatim",
         "conflict", "gap", "numeric_mismatch", "uncited_claim", "invalid_source", "empty_answer", "coverage"}
STAGES = {"queued", "planning", "retrieving", "synthesizing", "verifying", "writing_report", "publishing", "failed"}
REPAIRABLE = {"OUTPUT_JSON_INVALID", "OUTPUT_SCHEMA_INVALID", "FINDING_KIND_INVALID", "EVIDENCE_ID_UNKNOWN",
    "EVIDENCE_ID_DUPLICATE", "STEP_ID_UNKNOWN", "STEP_EVIDENCE_MISMATCH", "QUOTE_NOT_VERBATIM",
    "CONFLICT_UNRESOLVED", "EVIDENCE_GAP", "CITATION_INVALID", "CITATION_MISSING", "NUMERIC_MISMATCH", "COVERAGE_INCOMPLETE"}
LEGACY_MESSAGES = {
    "FINDING_KIND_INVALID": "Findings must be original evidence; analysis must be synthesis",
    "EVIDENCE_ID_UNKNOWN": "Invalid evidence or step binding",
    "EVIDENCE_ID_DUPLICATE": "Invalid evidence or step binding",
    "STEP_ID_UNKNOWN": "Invalid evidence or step binding",
    "STEP_EVIDENCE_MISMATCH": "Evidence does not cover the stated question",
    "QUOTE_NOT_VERBATIM": "Original evidence must be a verbatim quote",
    "CONFLICT_UNRESOLVED": "Unresolved conflicts cannot support synthesis",
    "EVIDENCE_GAP": "Evidence gaps cannot support synthesis",
    "CITATION_INVALID": "Citation verification failed",
    "CITATION_MISSING": "Citation verification failed",
    "NUMERIC_MISMATCH": "Citation verification failed",
    "COVERAGE_INCOMPLETE": "Research question coverage missing",
    "BUDGET_MODEL_CALLS": "Research budget exceeded",
    "BUDGET_TOKENS": "Research budget exceeded",
    "BUDGET_CONTEXT": "Research context budget exceeded",
    "RETRIEVAL_LIMIT": "Retrieval budget exceeded",
}


class ResearchFailure(ValueError):
    def __init__(self, code, *, stage=None, checks=None, error_type="ResearchValidationError", http_status=None):
        self.code = code if code in ERRORS else "EXECUTION_FAILED"
        self.stage, self.checks, self.error_type = stage, checks or [], error_type
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.prior_failure = None
        super().__init__(LEGACY_MESSAGES.get(self.code, "Research " + self.code.lower() + " failure"))


def check(rule, path=()):
    # Model-provided keys, values, source IDs and Pydantic input/ctx are never retained.
    return {"rule": rule if rule in RULES else "schema", "path": [
        part if (type(part) is int and 0 <= part <= 100) or (type(part) is str and part in FIELDS) else "*"
        for part in path[:8]]}


def schema_failure(exc, stage):
    return ResearchFailure("OUTPUT_SCHEMA_INVALID", stage=stage, error_type="ValidationError",
        checks=[check("schema", e["loc"]) for e in exc.errors(include_input=False, include_context=False, include_url=False)[:12]])


def describe(exc, stage):
    from fastapi import HTTPException
    from sqlalchemy.exc import SQLAlchemyError

    if isinstance(exc, ResearchFailure):
        return exc
    if isinstance(exc, ValidationError):
        return schema_failure(exc, stage)
    code = ("RESEARCH_TIMEOUT" if isinstance(exc, TimeoutError) else
            "ACCESS_REVOKED" if isinstance(exc, PermissionError) or isinstance(exc, HTTPException) and exc.status_code in {403, 404} else
            "RATE_LIMITED" if isinstance(exc, HTTPException) and exc.status_code == 429 else
            "PERSISTENCE_FAILED" if isinstance(exc, SQLAlchemyError) else
            "RETRIEVAL_FAILED" if stage == "retrieving" else "EXECUTION_FAILED")
    return ResearchFailure(code, stage=stage, error_type="ExecutionError")


def diagnostic(error, task, stage=None):
    phase = error.stage or stage or task.phase
    phase = "publishing" if phase == "completed" else phase
    result = {"code": error.code, "category": ERRORS[error.code][0], "message": ERRORS[error.code][1],
        "stage": phase if phase in STAGES else "failed", "error_type": error.error_type,
        "task_id": task.id, "at": utcnow().isoformat() + "Z",
        "checks": [check(c.get("rule"), c.get("path", ())) for c in error.checks[:12]],
        "context": {"steps": len(task.steps), "evidence_groups": len(task.evidence),
                    "model_calls": task.usage.get("model_calls", 0), "reserved_tokens": task.usage.get("reserved_tokens", 0)}}
    if error.http_status is not None:
        result["http_status"] = error.http_status
    if error.prior_failure:
        prior = error.prior_failure
        result["prior_failure"] = {"code": prior.code, "message": ERRORS[prior.code][1],
            "stage": prior.stage if prior.stage in STAGES else "verifying",
            "checks": [check(c.get("rule"), c.get("path", ())) for c in prior.checks[:12]]}
    return result


def prior_verification_error(events):
    """Expose a contiguous failed-repair sequence without rewriting historical rows."""
    terminal = next((i for i in range(len(events) - 1, -1, -1)
                     if events[i].get("phase") == "failed" and events[i].get("diagnostic")), None)
    if terminal is None:
        return None
    for i in range(terminal - 1, -1, -1):
        previous = events[i].get("diagnostic")
        if previous:
            if (events[i].get("phase") == "synthesizing" and previous.get("code") in REPAIRABLE and
                    all(e.get("phase") in {"synthesizing", "failed"} for e in events[i + 1:terminal + 1])):
                return previous
            break
    return None


def call_observations(usage):
    calls = usage.get("calls")
    known = isinstance(calls, list)
    returned = [c for c in (calls if known else []) if isinstance(c, dict) and c.get("status") == "returned"]
    return {"attempts": usage.get("model_calls", 0), "responses_received": len(returned) if known else None,
        "calls_with_usage": sum(isinstance(c.get("usage"), dict) and any(type(c["usage"].get(k)) is int for k in
            ("input_tokens", "output_tokens", "total_tokens")) for c in returned) if known else None,
        "network_send_count": None, "note": "应用尝试、收到响应和获得用量分开统计；外发次数需独立传输记录。"}


def log_diagnostic(data):
    logging.getLogger(__name__).warning("Research diagnostic task_id=%s stage=%s code=%s type=%s",
        data["task_id"], data["stage"], data["code"], data["error_type"])
