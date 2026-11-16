"""数据转换接口：维护转换脚本与转换数据集，覆盖登记、执行约束保存、节点重试与结果发布。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.transform import TransformService

router = APIRouter(prefix="/api/transform", tags=["数据转换"])

service = TransformService()

LIST_FIELDS = ["脚本编号", "输入数据源", "转换表达式", "输出结构", "依赖编排", "执行顺序", "超时秒数", "异常策略"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按脚本编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按脚本编号与状态过滤数据转换列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据转换清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "transform", "total": total, "items": items}


@router.get("/audits", response_model=PageResult[dict])
def list_audits(
    script: str | None = Query(default=None, description="脚本主键或脚本编号，如 TRN-268"),
    status: str | None = Query(default="待处理", description="待处理、已处理；留空返回全部"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """转换审计待处理区：失败节点会落到这里，接受重试记录并回显绑定的输入版本。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_audits(script=script, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/audits/{audit_id}/retry", response_model=ActionResult)
def retry_audit(audit_id: int, payload: EntryPayload) -> ActionResult:
    """在审计待处理区直接发起重试；输入版本留空时沿用审计记录绑定的版本。"""
    node, message = service.retry_audit(
        audit_id,
        input_version=payload.values.get("输入版本"),
        remark=payload.values.get("remark") or payload.remark,
        operator=payload.values.get("操作人"),
    )
    if node is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=node)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条转换脚本明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"转换脚本 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/dataset", response_model=dict)
def get_dataset(entry_id: str) -> dict:
    """读取转换数据集：节点状态、结果与输入版本绑定关系，供工作台与节点抽屉使用。

    entry_id 既可以是脚本主键，也可以是脚本编号（如 TRN-268）。
    """
    dataset = service.get_dataset(entry_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"转换脚本 {entry_id} 不存在或已归档")
    return dataset


@router.get("/{entry_id}/versions", response_model=dict)
def get_versions(entry_id: str) -> dict:
    """读取依赖图下可用的输入版本及完整性，为重试与发布提供版本选择。"""
    dataset = service.get_dataset(entry_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"转换脚本 {entry_id} 不存在或已归档")
    return {
        "脚本编号": dataset["脚本编号"],
        "依赖图": dataset["依赖图"],
        "versions": dataset["可用输入版本"],
        "工作台输入版本": dataset["工作台输入版本"],
        "结果输入版本集合": dataset["结果输入版本集合"],
    }


@router.get("/{entry_id}/nodes/{node_id}", response_model=dict)
def get_node(entry_id: str, node_id: str) -> dict:
    """读取单个节点明细：节点抽屉据此回显输入版本、结果版本与审计记录。"""
    node = service.get_node(entry_id, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 不在转换脚本 {entry_id} 的转换数据集里")
    return node


@router.post("/{entry_id}/nodes/{node_id}/retry", response_model=ActionResult)
def retry_node(entry_id: str, node_id: str, payload: EntryPayload) -> ActionResult:
    """重试失败节点：显式指定的输入版本优先，其次节点锁定版本，最后取依赖图最新完整版本。"""
    node, message = service.retry_node(
        entry_id,
        node_id,
        input_version=payload.values.get("输入版本"),
        remark=payload.values.get("remark") or payload.remark,
        operator=payload.values.get("操作人"),
    )
    if node is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=node)


@router.post("/{entry_id}/publish", response_model=ActionResult)
def publish(entry_id: str, payload: EntryPayload) -> ActionResult:
    """发布转换结果：只有全部节点成功且结果基于一致依赖版本时才允许发布。"""
    release, message = service.publish(
        entry_id,
        input_version=payload.values.get("输入版本"),
        operator=payload.values.get("操作人"),
    )
    if release is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=release)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条转换脚本，缺字段时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=errors[0])
    return ActionResult(ok=True, message="转换脚本已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """保存转换脚本：依赖编排、执行顺序、超时秒数与异常策略随脚本一起持久化。"""
    entry, errors = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=errors[0])
    return ActionResult(ok=True, message="转换脚本已保存", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条转换脚本执行提交校验、启用脚本、停用脚本；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
