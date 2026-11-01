"""Pure offline scoring: no retrieval, embedding, generation or model judging.

Null scores and unknown denominators are deliberate. A verbatim quotation is
only checked for textual attribution, not relevance, truth or entailment.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.research_evaluation_schemas import HumanReview, ResearchCase
from app.research_schemas import ResearchPlan
from app.services.evidence import clean_text
from app.services.research import validate_synthesis
from app.services.research_errors import ERRORS, ResearchFailure
from app.services.research_errors import check as diagnostic_check

VERSION = "research-evaluation-v2"
RULES = {
    "task_completion": "人工逐预期要点评审；分母为case.expected_points，不以步骤completed代替。",
    "evidence_coverage": "固定原文锚点在有效来源证据中的逐字覆盖/全部锚点；只测文本可用性，不是语义召回。",
    "citation_validity": "有效finding→evidence绑定/全部绑定；需来源chunk/doc/revision/KB/page/locator/文本可追溯及step绑定。",
    "fact_support": "逐发现/分析单元；原文项仅自动核验文字归属，综合项待人工语义复核；非原子事实真实性。",
    "conflict_identification": "已标注冲突对命中/全部预期冲突对；合成case只检验potential flags，不宣称语义矛盾识别。",
    "result_consistency": "相同case/配置下独立执行结果的人工语义一致性；单次执行或重复读取不可判定。",
    "plan_quality": "人工评审规划是否覆盖目标、拆分合理且保持约束；结构验证另列工程检查。",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def code_version():
    root = Path(__file__).resolve().parents[1]
    names = ["services/research_evaluation.py", "research_evaluation_schemas.py",
             "services/research.py", "research_schemas.py", "services/verification.py",
             "services/research_errors.py"]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def metric(units, method, denominator_known=True):
    counts = {v: sum(u["verdict"] == v for u in units) for v in ("pass", "fail", "unknown")}
    decided = counts["pass"] + counts["fail"]
    total = len(units) if denominator_known else None
    return {"numerator": counts["pass"], "denominator": total, "valid_samples": decided,
            "pending": counts["unknown"], "failed": counts["fail"],
            "score": counts["pass"] / total if total and not counts["unknown"] else None,
            "reviewed_subset_score": counts["pass"] / decided if decided else None,
            "status": "insufficient_evidence" if not denominator_known else "not_applicable" if not total else
                      "pending_review" if counts["unknown"] else "scored",
            "method": method, "units": units}


def unit(uid, verdict="unknown", reason="需要人工复核", **refs):
    return {"unit_id": uid, "verdict": verdict, "reason": reason, **refs}


def evaluate_research(case, task, sources, *, benchmark_version, model_config=None, review=None, comparison_task=None):
    """sources maps chunk IDs to read-only snapshots, not model-supplied citations.

    Each source includes text, doc/revision/KB/name/page/locator. Missing historical
    chunks are unknown, never silently accepted. Reviews bind exact task/case hashes.
    """
    task = json.loads(json.dumps(task, ensure_ascii=False, default=str))
    case = ResearchCase.model_validate(case).model_dump()
    task_hash, case_hash, source_hash = digest(task), digest(case), digest(sources)
    if case["question"] != task["question"]:
        raise ValueError("Case question differs from saved research question")
    if task.get("restricted"):
        raise ValueError("Restricted research cannot be evaluated")
    if task.get("status") not in {"completed", "failed", "cancelled"}:
        raise ValueError("Only terminal research snapshots can be evaluated")
    checks = []
    def check(stage, name, passed, reason, **refs):
        checks.append({"stage": stage, "check": name, "status": "unknown" if passed is None else
                       "passed" if passed else "failed", "reason": reason, **refs})
    try:
        plan = task.get("plan") or {}
        ResearchPlan.model_validate({k: plan[k] for k in ("objective", "steps")})
        check("plan", "plan_contract", True, "1–4个连续且不重复的子任务；不证明规划质量")
    except (ValueError, KeyError, TypeError):
        check("plan", "plan_contract", False, "计划缺失或不满足严格结构")
    steps = task.get("steps") or []
    by_step = {s["id"]: s for s in steps}
    evidence = task.get("evidence") or []
    by_id = {e["id"]: e for e in evidence}
    check("retrieval", "unique_records", len(by_id) == len(evidence) and len(by_step) == len(steps), "步骤/证据ID唯一")
    planned = (task.get("plan") or {}).get("steps", [])
    check("plan", "plan_execution_binding", bool(planned) and
          [{k: s.get(k) for k in ("id", "question", "expected_output")} for s in planned] ==
          [{k: s.get(k) for k in ("id", "question", "expected_output")} for s in steps],
          "保存计划与执行步骤的ID、顺序、问题和预期输出一致；非语义完成度")
    check("retrieval", "execution_trace", bool(task.get("events")) and bool(steps) and all(
        s.get("rounds") and all(r.get("query") and "retrieved" in r and "accepted" in r and r.get("at")
                               for r in s["rounds"]) for s in steps), "执行事件、查询、时间、召回及接纳ID可追溯")
    check("retrieval", "step_evidence_bindings", all(
        eid in by_id and s["id"] in by_id[eid].get("step_ids", [])
        for s in steps for eid in s.get("evidence_ids", [])) and all(
        e.get("step_ids") and all(sid in by_step and e["id"] in by_step[sid].get("evidence_ids", [])
                                  for sid in e["step_ids"]) for e in evidence), "步骤与证据的双向绑定均存在且一致")
    origin_checks, evidence_status = [], {}
    for e in evidence:
        statuses = []
        origins = e.get("origins") or [e]
        for i, origin in enumerate(origins):
            source = sources.get(origin.get("chunk_id"))
            valid = None
            reason = "原始历史分块不可用，无法核实"
            if source:
                valid = all(origin.get(k) == source.get(k) for k in
                            ("document_id", "revision_id", "knowledge_base_id", "document_name", "page", "locator"))
                in_scope = (source.get("document_name") in case["material_scope"] and
                            source.get("knowledge_base_id") in task.get("knowledge_base_ids", []))
                valid = valid and in_scope
                # Fuzzy grouping may have variant text. Such origins stay unknown;
                # only exact attribution is accepted automatically.
                if valid and e.get("text") not in clean_text(source["text"]):
                    valid = None
                    reason = "归组片段与此来源并非连续相同文本，需复核近重复归属"
                else:
                    reason = ("来源身份、版本、位置及连续文本" if valid else
                              "来源超出Case资料范围或任务知识库范围" if not in_scope else "来源身份/版本/位置不匹配")
            verdict = "unknown" if valid is None else "pass" if valid else "fail"
            origin_checks.append(unit(f"{e['id']}.origin.{i}", verdict, reason, evidence_id=e["id"], origin=origin))
            statuses.append(verdict)
            check("retrieval", "source_attribution", valid, reason, evidence_id=e["id"], origin=origin)
        evidence_status[e["id"]] = "fail" if "fail" in statuses else "unknown" if "unknown" in statuses else "pass"
    result = task.get("result") or {}
    findings = [(f"findings.{i}", f) for i, f in enumerate(result.get("findings", []))]
    findings += [(f"analysis.{i}", f) for i, f in enumerate(result.get("analysis", []))]
    citations, facts, unverified_facts = [], [], set()
    for fid, finding in findings:
        ids, sids = finding.get("evidence_ids", []), finding.get("step_ids", [])
        binding = bool(ids and sids) and len(ids) == len(set(ids)) and all(
            sid in by_step and any(sid in by_id.get(eid, {}).get("step_ids", []) and
                                  eid in by_step[sid].get("evidence_ids", []) for eid in ids) for sid in sids)
        binding = binding and all(any(sid in by_id.get(eid, {}).get("step_ids", []) and
                                      eid in by_step[sid].get("evidence_ids", []) for sid in sids) for eid in ids)
        binding = binding and len(by_id) == len(evidence) and len(by_step) == len(steps)
        for i, eid in enumerate(ids):
            state = evidence_status.get(eid, "fail") if binding else "fail"
            citations.append(unit(f"{fid}.citation.{i}", state, "来源和子问题绑定；不验证语义蕴含",
                                  finding_id=fid, evidence_id=eid, step_ids=sids))
        exact = finding.get("kind") == "evidence" and any(
            finding.get("text") and finding["text"] in by_id.get(eid, {}).get("text", "") and
            evidence_status.get(eid) == "pass" for eid in ids)
        invalid = not binding or any(eid not in by_id or evidence_status.get(eid) == "fail" for eid in ids)
        if invalid or any(evidence_status.get(eid) != "pass" for eid in ids):
            unverified_facts.add(fid)
        facts.append(unit(fid, "fail" if invalid else "pass" if exact else "unknown",
                          "连续原文归属已验证；不证明相关性或客观真实" if exact and not invalid else
                          "引用绑定失败" if invalid else "综合判断/非逐字归属需人工语义复核",
                          text=finding.get("text"), evidence_ids=ids, step_ids=sids,
                          support_scope="textual_attribution" if exact else "semantic_review"))
    if task["status"] == "completed":
        try:
            data = {k: result[k] for k in ("findings", "analysis", "limitations", "follow_up_questions")}
            # Persisted findings include the server's previous verdict. Replay
            # recomputes it rather than feeding output metadata into model input.
            data = {**data, **{key: [{k: v for k, v in f.items() if k != "verification"}
                                    for f in data[key]] for key in ("findings", "analysis")}}
            # Settings are explicitly offline; validation only, never answer generation.
            if evidence:
                validate_synthesis(data, evidence, steps, Settings(llm_model="", llm_api_key=""), result.get("conflicts", []))
            else:
                if result.get("findings") or result.get("analysis") or not result.get("limitations"):
                    raise ValueError("No evidence must abstain")
            check("synthesis", "synthesis_contract", True, "结构/原文/引用/数字/覆盖校验；非完整语义核验")
        except ResearchFailure as exc:
            check("synthesis", "synthesis_contract", False, ERRORS[exc.code][1], code=exc.code,
                  validation_checks=[diagnostic_check(c.get("rule"), c.get("path", ())) for c in exc.checks])
        except (ValueError, KeyError, TypeError):
            check("synthesis", "synthesis_contract", False, "研究综合未满足结构、原文、引用或覆盖约束")
    else:
        check("conclusion", "terminal_result", False, task.get("error") or f"任务终态为{task['status']}，无完成结论")
    anchors = []
    anchor_evidence = {}
    for anchor in case["anchors"]:
        # Full-source file fingerprint is asserted by the benchmark loader. The
        # saved result also records this annotation; matching does not prove recall.
        matches = [e["id"] for e in evidence if e.get("document_name") == anchor["document_name"] and
                   anchor["quote"] in e.get("text", "")]
        anchor_evidence[anchor["id"]] = matches
        versioned = [eid for eid in matches if any(sources.get(o.get("chunk_id"), {}).get("source_sha256") ==
                     anchor["source_sha256"] for o in (by_id[eid].get("origins") or [by_id[eid]]))]
        verified = any(evidence_status.get(eid) == "pass" for eid in versioned)
        unknown = matches and not verified and (not versioned or any(
            evidence_status.get(eid) == "unknown" for eid in versioned))
        anchors.append(unit(anchor["id"], "pass" if verified else "unknown" if unknown else "fail",
                            "固定原文锚点的文本可用性代理；非语义相关性", evidence_ids=matches, anchor=anchor))
    conflict_units = []
    for i, pair in enumerate(case["expected_conflicts"] or []):
        matched = any(any(a in flag.get("evidence_ids", []) for a in anchor_evidence[pair[0]]) and
                      any(b in flag.get("evidence_ids", []) for b in anchor_evidence[pair[1]])
                      for flag in result.get("conflicts", []))
        synthetic = case["provenance"] == "synthetic_fixture"
        conflict_units.append(unit(f"conflict.{i}", "pass" if synthetic and matched else "fail" if synthetic else "unknown",
                                   "合成冲突对的potential flag匹配；真实语义冲突待人工复核", anchor_ids=pair))
    units = {
        "task_completion": [unit(f"point.{i}", expected_point=p) for i, p in enumerate(case["expected_points"])],
        "evidence_coverage": anchors, "citation_validity": citations, "fact_support": facts,
        "conflict_identification": conflict_units, "result_consistency": [],
        "plan_quality": [unit("plan")],
    }
    comparison = None
    if comparison_task:
        comparison = json.loads(json.dumps(comparison_task, ensure_ascii=False, default=str))
        config = task.get("usage", {}).get("configuration")
        other_config = comparison.get("usage", {}).get("configuration")
        if (comparison.get("restricted") or comparison.get("id") == task["id"] or
                comparison.get("question") != task["question"] or comparison.get("status") != "completed" or
                task["status"] != "completed" or not config or config != other_config or
                set(comparison.get("knowledge_base_ids", [])) != set(task.get("knowledge_base_ids", []))):
            raise ValueError("Independent-run comparison requires distinct completed tasks, same question/scope and recorded configuration")
        # Independence cannot be inferred solely from IDs: human review must
        # establish that these are separate executions against the same source versions.
        units["result_consistency"] = [unit("pair.0", reason="需人工确认独立执行、相同来源版本和结论语义一致性",
                                                 peer_task_id=comparison["id"], peer_sha256=digest(comparison))]
    review_data = None
    if review:
        review_data = HumanReview.model_validate(review).model_dump()
        if review_data["task_sha256"] != task_hash or review_data["case_sha256"] != case_hash:
            raise ValueError("Stale review: case/task digest differs")
        if review_data["source_snapshot_sha256"] != source_hash:
            raise ValueError("Stale review: source snapshot digest differs")
        if review_data["comparison_sha256"] != (digest(comparison) if comparison else None):
            raise ValueError("Stale review: comparison digest differs")
        for decision in review_data["decisions"]:
            target = next((u for u in units[decision["metric"]] if u["unit_id"] == decision["unit_id"]), None)
            if target is None:
                raise ValueError("Unknown review unit; independent-run consistency needs paired protocol")
            if decision["metric"] == "fact_support" and (
                    target["verdict"] == "fail" and decision["verdict"] != "fail" or
                    decision["unit_id"] in unverified_facts and decision["verdict"] == "pass"):
                raise ValueError("Human semantic review cannot override failed or unverified source/binding checks")
            target.update(automatic_verdict=target["verdict"], automatic_reason=target["reason"])
            target.update(verdict=decision["verdict"], reason=decision["rationale"],
                          human_review=decision)
    metrics = {name: {**metric(value, RULES[name], denominator_known=(name != "result_consistency" or comparison is not None) and
                     (name != "conflict_identification" or case["expected_conflicts"] is not None)),
                     "definition": RULES[name]} for name, value in units.items()}
    failed = [c for c in checks if c["status"] == "failed"]
    return {"case_id": case["id"], "case_reference": case, "split": case["split"], "scenario": case["scenario"],
            "question": task["question"], "task_id": task["id"], "task_status": task["status"],
            "benchmark_version": benchmark_version, "evaluator_version": VERSION, "code_sha256": code_version(),
            "executed_at": datetime.now(UTC).isoformat(), "task_sha256": task_hash, "case_sha256": case_hash,
            "source_snapshot_sha256": source_hash, "model_config": model_config or
            task.get("usage", {}).get("configuration") or {"model_observed": result.get("provider"),
                "historical_configuration": "not_recorded", "execution_version": "not_recorded"},
            "engineering": {"status": "failed" if failed else "inconclusive" if any(c["status"] == "unknown" for c in checks) else "passed",
                            "checks": checks}, "metrics": metrics, "source_attribution": origin_checks,
            "failures": failed + [{"stage": "quality", "metric": name, **u} for name, m in metrics.items()
                                   for u in m["units"] if u["verdict"] == "fail"],
            "quality_status": "尚未完成质量验证" if any(m["status"] in {"pending_review", "insufficient_evidence"}
                                                           for m in metrics.values()) else "scored_without_acceptance_threshold",
            "review": review_data, "model_assisted_evaluation": "not_run",
            "comparison_task": comparison,
            "execution_record": {k: task.get(k) for k in ("plan", "steps", "events", "usage", "error", "error_details", "created_at", "updated_at")},
            "evidence": evidence, "result": result,
            "new_model_calls": 0, "new_input_tokens": 0, "new_output_tokens": 0, "estimated_cost_cny": 0}
