"""数据转换业务规则：状态流转、字段校验与筛选口径都收在这里。

除了转换脚本本身，这里还维护转换数据集（每个节点的执行结果）、转换审计与发布记录。
关键约束：
- 登记/修改脚本时，依赖编排、执行顺序、超时秒数与异常策略必须一起落库；
- 节点失败时保留上一版结果，并写入转换审计待处理区；
- 重试只能绑定一组完整且一致的依赖输入版本，优先级为「显式指定 > 节点锁定 > 最新完整版本」；
- 只有全部节点成功且结果输入版本一致时，才允许发布。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "transform"
REQUIRED_FIELDS = ["脚本编号", "输入数据源", "转换表达式"]
OPTIONAL_FIELDS = ["输出结构", "依赖编排", "执行顺序", "超时秒数", "异常策略"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用脚本": "已启用", "停用脚本": "已停用"}
NEGATIVE_ACTIONS = ["停用脚本"]
DEFAULT_ERROR_POLICY = "失败保留上一版并写入转换审计"

VERSION_TABLE = "transform_version"
DATASET_TABLE = "transform_dataset"
AUDIT_TABLE = "transform_audit"
RELEASE_TABLE = "transform_release"

PENDING_AUDIT = "待处理"
DONE_AUDIT = "已处理"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


class TransformService:
    # ------------------------------------------------------------------ 脚本
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("脚本编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def _validate_values(self, values: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        order = _text(values.get("执行顺序"))
        if order:
            try:
                if int(order) < 1:
                    errors.append("执行顺序必须是大于 0 的整数")
            except ValueError:
                errors.append("执行顺序必须是大于 0 的整数")
        timeout = _text(values.get("超时秒数"))
        if timeout:
            try:
                seconds = int(timeout)
                if seconds < 1 or seconds > 86400:
                    errors.append("超时秒数需在 1 到 86400 之间")
            except ValueError:
                errors.append("超时秒数需在 1 到 86400 之间")
        return errors

    def _apply_values(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        # 只覆盖提交上来的字段：局部保存（例如只改超时）不会把未提交的必填字段清空。
        for field in REQUIRED_FIELDS:
            if field in values:
                entry[field] = values.get(field)
        for field in OPTIONAL_FIELDS:
            if field in values and values.get(field) is not None:
                entry[field] = values.get(field)
            elif field not in entry:
                entry[field] = ""
        for field in ("执行顺序", "超时秒数"):
            if _text(entry.get(field)):
                entry[field] = int(_text(entry.get(field)))
        if not _text(entry.get("异常策略")):
            entry["异常策略"] = DEFAULT_ERROR_POLICY

    def _duplicate_script_no(self, script_no: str, exclude_id: int | None = None) -> bool:
        return any(
            _text(row.get("脚本编号")) == script_no and int(row.get("id", 0)) != exclude_id
            for row in store.rows(MODULE)
        )

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        if self._duplicate_script_no(_text(values.get("脚本编号"))):
            return None, [f"脚本编号 {values.get('脚本编号')} 已登记，不能重复登记"]
        errors = self._validate_values(values)
        if errors:
            return None, errors
        rows = store.rows(MODULE)
        entry = {"id": _next_id(rows)}
        self._apply_values(entry, values)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, [f"转换脚本 {entry_id} 不存在或已归档"]
        script_no = _text(values.get("脚本编号"))
        if script_no and self._duplicate_script_no(script_no, exclude_id=entry_id):
            return None, [f"脚本编号 {script_no} 已被占用，不能重复登记"]
        errors = self._validate_values(values)
        if errors:
            return None, errors
        old_script_no = self._script_no(entry)
        self._apply_values(entry, values)
        new_script_no = self._script_no(entry)
        graph = _text(entry.get("依赖编排"))
        # 依赖编排或脚本编号改了，转换数据集与审计记录要跟着改，
        # 否则重试会去旧依赖图、旧脚本编号里找输入版本。
        for node in self._nodes(old_script_no):
            node["依赖图"] = graph
            if new_script_no != old_script_no:
                node["脚本编号"] = new_script_no
        if new_script_no != old_script_no:
            for table in (AUDIT_TABLE, RELEASE_TABLE):
                for row in store.support_rows(table):
                    if _text(row.get("脚本编号")) == old_script_no:
                        row["脚本编号"] = new_script_no
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"转换脚本 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据转换可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"转换脚本已{action}"

    # ------------------------------------------------------------- 转换数据集
    def _resolve_script(self, key: Any) -> dict[str, Any] | None:
        """脚本既支持按数字主键，也支持按脚本编号（如 TRN-268）查询。"""
        if isinstance(key, int) or _text(key).isdigit():
            return store.find(MODULE, int(key))
        for row in store.rows(MODULE):
            if _text(row.get("脚本编号")) == _text(key):
                return row
        return None

    def _script_no(self, script: dict[str, Any]) -> str:
        return _text(script.get("脚本编号"))

    def _nodes(self, script_no: str) -> list[dict[str, Any]]:
        rows = [
            row
            for row in store.support_rows(DATASET_TABLE)
            if _text(row.get("脚本编号")) == script_no
        ]
        return sorted(rows, key=lambda row: (int(row.get("执行顺序") or 0), int(row.get("id") or 0)))

    def _node_status(self, node: dict[str, Any]) -> str:
        """失败节点的显示状态以失败为准，不能因为历史标记看起来像成功就当成成功。"""
        status = _text(node.get("状态")) or "待执行"
        if bool(node.get("待重试")) and status == "成功":
            return "失败"
        return status

    def _versions(self, graph: str) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in store.support_rows(VERSION_TABLE)
            if _text(row.get("依赖图")) == graph
        ]
        return sorted(rows, key=lambda row: int(row.get("id") or 0))

    def _version(self, graph: str, version: str) -> dict[str, Any] | None:
        for row in self._versions(graph):
            if _text(row.get("输入版本")) == _text(version):
                return row
        return None

    @staticmethod
    def _missing_nodes(version_row: dict[str, Any] | None, node_ids: list[str]) -> list[str]:
        if version_row is None:
            return list(node_ids)
        snapshot = version_row.get("节点快照") or {}
        return [node_id for node_id in node_ids if node_id not in snapshot]

    def _is_complete(self, graph: str, version: str, node_ids: list[str]) -> bool:
        row = self._version(graph, version)
        if row is None or not row.get("完整性"):
            return False
        return not self._missing_nodes(row, node_ids)

    def _latest_complete(self, graph: str, node_ids: list[str]) -> str:
        candidates = [
            _text(row.get("输入版本"))
            for row in self._versions(graph)
            if row.get("完整性") and not self._missing_nodes(row, node_ids)
        ]
        return candidates[-1] if candidates else ""

    def _version_rows(self, graph: str) -> list[dict[str, Any]]:
        rows = []
        for row in self._versions(graph):
            rows.append({
                "输入版本": _text(row.get("输入版本")),
                "完整性": bool(row.get("完整性")),
                "生成时间": row.get("生成时间") or "",
                "来源": row.get("来源") or "",
            })
        return rows

    # ------------------------------------------------------------------ 审计
    def _find_node(self, script_no: str, node_id: str) -> dict[str, Any] | None:
        for row in self._nodes(script_no):
            if _text(row.get("节点编号")) == _text(node_id):
                return row
        return None

    def _pending_audits(self, script_no: str, node_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in store.support_rows(AUDIT_TABLE)
            if _text(row.get("脚本编号")) == script_no
            and _text(row.get("节点编号")) == _text(node_id)
            and _text(row.get("状态")) == PENDING_AUDIT
        ]

    def _append_audit(
        self,
        *,
        script_no: str,
        graph: str,
        node: dict[str, Any],
        kind: str,
        status: str,
        input_version: str,
        note: str,
        previous_version: str = "",
        handle_note: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = store.support_rows(AUDIT_TABLE)
        sequence = len([row for row in rows if _text(row.get("脚本编号")) == script_no]) + 1
        now = _now()
        record = {
            "id": _next_id(rows),
            "审计编号": f"AUD-{script_no}-{sequence:03d}",
            "脚本编号": script_no,
            "依赖图": graph,
            "节点编号": _text(node.get("节点编号")),
            "节点名称": node.get("节点名称") or "",
            "类型": kind,
            "状态": status,
            "输入版本": input_version,
            "上一版结果版本": previous_version,
            "说明": note,
            "创建时间": now,
            "处理时间": now if status == DONE_AUDIT else "",
            "处理说明": handle_note,
        }
        if extra:
            record.update(extra)
        rows.append(record)
        return record

    def _close_pending_audits(self, script_no: str, node_id: str, note: str) -> None:
        now = _now()
        for row in self._pending_audits(script_no, node_id):
            row["状态"] = DONE_AUDIT
            row["处理时间"] = now
            row["处理说明"] = note

    def _sync_pending_audits(self, script_no: str, graph: str) -> None:
        """待重试/失败节点必须有对应的审计待处理记录，工作台与审计区共用同一份输入版本。"""
        for node in self._nodes(script_no):
            failed = self._node_status(node) == "失败" or bool(node.get("待重试"))
            if failed and not self._pending_audits(script_no, _text(node.get("节点编号"))):
                self._append_audit(
                    script_no=script_no,
                    graph=graph,
                    node=node,
                    kind="执行失败",
                    status=PENDING_AUDIT,
                    input_version=_text(node.get("输入版本")),
                    note=(
                        f"节点执行失败，保留上一版结果 {node.get('结果版本') or '无'}"
                        f"（基于 {node.get('结果输入版本') or '未知输入版本'}），等待重试"
                    ),
                    previous_version=_text(node.get("结果版本")),
                )

    def list_audits(
        self,
        *,
        script: str | None = None,
        status: str | None = PENDING_AUDIT,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.support_rows(AUDIT_TABLE)
        script_no = ""
        if script:
            script_row = self._resolve_script(script)
            if script_row is None:
                return [], 0
            script_no = self._script_no(script_row)
        targets = [script_no] if script_no else [
            self._script_no(script_row) for script_row in store.rows(MODULE)
        ]
        for name in targets:
            script_row = self._resolve_script(name)
            if script_row is not None:
                self._sync_pending_audits(name, _text(script_row.get("依赖编排")))
        rows = list(store.support_rows(AUDIT_TABLE))
        if script_no:
            rows = [row for row in rows if _text(row.get("脚本编号")) == script_no]
        if status:
            rows = [row for row in rows if _text(row.get("状态")) == status]
        total = len(rows)
        rows = sorted(rows, key=lambda row: int(row.get("id") or 0), reverse=True)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # ------------------------------------------------------------------ 节点
    def _node_view(self, node: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
        view = dict(node)
        status = self._node_status(node)
        result_version = _text(node.get("结果版本"))
        result_input = _text(node.get("结果输入版本"))
        input_version = _text(node.get("输入版本"))
        waiting = bool(node.get("待重试"))
        deps = [_text(dep) for dep in (node.get("依赖节点") or [])]
        node_map = {_text(item.get("节点编号")): item for item in nodes}
        upstream: list[dict[str, Any]] = []
        conflicts: list[str] = []
        for dep_id in deps:
            dep = node_map.get(dep_id)
            if dep is None:
                conflicts.append(f"上游节点 {dep_id} 不存在")
                continue
            dep_input = _text(dep.get("结果输入版本"))
            upstream.append({
                "节点编号": dep_id,
                "输入版本": _text(dep.get("输入版本")),
                "结果输入版本": dep_input,
                "结果版本": _text(dep.get("结果版本")),
                "状态": self._node_status(dep),
            })
            if dep_input != input_version:
                conflicts.append(f"上游 {dep_id} 结果基于 {dep_input or '未知'}，与节点输入版本 {input_version or '未知'} 不一致")
            if self._node_status(dep) != "成功":
                conflicts.append(f"上游 {dep_id} 尚未成功")
        view.update({
            "显示状态": status,
            "失败": status == "失败",
            "待重试": waiting,
            "结果存在": bool(result_version),
            "结果输入版本": result_input,
            "结果与输入版本一致": bool(result_version) and result_input == input_version,
            "结果已过期": bool(result_version) and result_input != input_version,
            "依赖输入版本冲突": conflicts,
            "上游节点": upstream,
            "可重试": status == "失败" or waiting,
        })
        return view

    def get_dataset(self, key: Any) -> dict[str, Any] | None:
        script = self._resolve_script(key)
        if script is None:
            return None
        script_no = self._script_no(script)
        graph = _text(script.get("依赖编排"))
        nodes = self._nodes(script_no)
        self._sync_pending_audits(script_no, graph)
        node_ids = [_text(node.get("节点编号")) for node in nodes]
        views = [self._node_view(node, nodes) for node in nodes]
        result_versions = {
            _text(node.get("结果输入版本"))
            for node in nodes
            if _text(node.get("结果版本"))
        }
        consistent = len(result_versions) == 1 and bool(next(iter(result_versions), ""))
        # 工作台与审计待处理区读同一个输入版本：优先取待重试/失败节点绑定的版本。
        waiting = next(
            (_text(node.get("输入版本")) for node in nodes if node.get("待重试") or self._node_status(node) == "失败"),
            "",
        )
        if waiting:
            workbench_version = waiting
        elif consistent:
            workbench_version = next(iter(result_versions))
        else:
            workbench_version = _text(script.get("输入数据源"))
        blockers = self._publish_blockers(nodes, node_ids)
        pending_audits = [
            row for row in store.support_rows(AUDIT_TABLE)
            if _text(row.get("脚本编号")) == script_no and _text(row.get("状态")) == PENDING_AUDIT
        ]
        return {
            "脚本编号": script_no,
            "脚本主键": script.get("id"),
            "依赖图": graph,
            "工作台输入版本": workbench_version,
            "审计待处理输入版本": sorted({
                _text(row.get("输入版本")) for row in pending_audits
            }),
            "结果输入版本集合": sorted(version for version in result_versions if version),
            "输入版本一致": consistent,
            "节点": views,
            "节点数": len(views),
            "成功节点数": sum(1 for node in nodes if self._node_status(node) == "成功"),
            "失败节点数": sum(1 for node in nodes if self._node_status(node) == "失败" or node.get("待重试")),
            "待重试节点数": sum(1 for node in nodes if node.get("待重试")),
            "待处理审计数": len(pending_audits),
            "可发布": not blockers,
            "发布阻塞原因": blockers,
            "可用输入版本": self._version_rows(graph),
            "发布时间": (
                store.support_rows(RELEASE_TABLE)[-1].get("发布时间")
                if store.support_rows(RELEASE_TABLE) else ""
            ),
        }

    def get_node(self, key: Any, node_id: str) -> dict[str, Any] | None:
        script = self._resolve_script(key)
        if script is None:
            return None
        script_no = self._script_no(script)
        graph = _text(script.get("依赖编排"))
        nodes = self._nodes(script_no)
        node = self._find_node(script_no, node_id)
        if node is None:
            return None
        self._sync_pending_audits(script_no, graph)
        view = self._node_view(node, nodes)
        view["依赖图"] = graph
        view["审计记录"] = [
            dict(row)
            for row in store.support_rows(AUDIT_TABLE)
            if _text(row.get("脚本编号")) == script_no
            and _text(row.get("节点编号")) == _text(node_id)
        ]
        view["发布阻塞原因"] = self._publish_blockers(nodes, [_text(item.get("节点编号")) for item in nodes])
        return view

    # ------------------------------------------------------------------ 重试
    def _conflicts(self, node: dict[str, Any], nodes: list[dict[str, Any]], version: str) -> list[str]:
        node_map = {_text(item.get("节点编号")): item for item in nodes}
        conflicts: list[str] = []
        for dep_id in [_text(dep) for dep in (node.get("依赖节点") or [])]:
            dep = node_map.get(dep_id)
            if dep is None:
                conflicts.append(f"上游节点 {dep_id} 不存在")
                continue
            if self._node_status(dep) != "成功":
                conflicts.append(f"上游节点 {dep_id} 尚未成功，不能作为重试输入")
                continue
            dep_version = _text(dep.get("结果输入版本"))
            if dep_version != version:
                conflicts.append(
                    f"上游节点 {dep_id} 结果基于 {dep_version or '未知输入版本'}，"
                    f"与重试输入版本 {version} 不一致"
                )
        return conflicts

    def retry_node(
        self,
        key: Any,
        node_id: str,
        *,
        input_version: str | None = None,
        remark: str | None = None,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        script = self._resolve_script(key)
        if script is None:
            return None, f"转换脚本 {key} 不存在或已归档"
        script_no = self._script_no(script)
        graph = _text(script.get("依赖编排"))
        nodes = self._nodes(script_no)
        node = self._find_node(script_no, node_id)
        if node is None:
            return None, f"转换数据集里找不到节点 {node_id}"
        if self._node_status(node) != "失败" and not node.get("待重试"):
            return None, f"节点 {node_id} 当前状态为「{node.get('状态') or '待执行'}」，只有失败或待重试节点可以重试"
        node_ids = [_text(item.get("节点编号")) for item in nodes]
        latest = self._latest_complete(graph, node_ids)
        locked = _text(node.get("输入版本"))
        requested = _text(input_version)

        # 优先级：显式指定 > 节点锁定 > 依赖图最新完整版本。
        if requested:
            version, priority = requested, "显式指定输入版本"
        elif locked:
            version, priority = locked, "节点锁定输入版本"
        elif latest:
            version, priority = latest, "依赖图最新完整版本"
        else:
            return None, f"依赖图 {graph} 没有完整输入版本，节点 {node_id} 无法重试"

        if not self._is_complete(graph, version, node_ids):
            version_row = self._version(graph, version)
            missing = self._missing_nodes(version_row, node_ids)
            if requested:
                return None, (
                    f"输入版本 {version} 不完整，缺少节点 {'、'.join(missing) or '快照'}，"
                    "不能作为重试输入"
                )
            if latest and latest != version:
                version, priority = latest, "依赖图最新完整版本（节点锁定版本不完整）"
            else:
                return None, f"输入版本 {version} 不完整，依赖图没有可用的完整输入版本"

        conflicts = self._conflicts(node, nodes, version)
        if conflicts:
            # 冲突记录仍然绑定节点当前锁定的输入版本（与工作台一致），
            # 被拒绝的候选版本放在「请求输入版本」里，避免待处理区与工作台版本对不上。
            bound = locked or version
            self._append_audit(
                script_no=script_no,
                graph=graph,
                node=node,
                kind="重试冲突",
                status=PENDING_AUDIT,
                input_version=bound,
                note=(
                    f"重试输入版本冲突（{priority}）：请求 {version}，节点继续绑定 {bound}。"
                    + "；".join(conflicts)
                    + "。节点保留上一版结果，等待人工判定输入版本"
                ),
                previous_version=_text(node.get("结果版本")),
                extra={
                    "请求输入版本": version,
                    "优先级": priority,
                    "冲突原因": conflicts,
                },
            )
            node["重试冲突原因"] = conflicts
            node["重试冲突版本"] = version
            return None, "重试输入版本冲突：" + "；".join(conflicts)

        previous_result = _text(node.get("结果版本"))
        previous_input = _text(node.get("结果输入版本"))
        suffix = version.rsplit("-", 1)[-1]
        new_result = f"RES-{node_id.replace('N-', '')}-{suffix}"
        history = list(node.get("结果历史") or [])
        if previous_result:
            history.append({
                "结果版本": previous_result,
                "输入版本": previous_input,
                "保留原因": "重试前保留上一版成品",
                "时间": _text(node.get("最近执行时间")),
            })
        node.update({
            "输入版本": version,
            "状态": "成功",
            "本次结果版本": new_result,
            "结果版本": new_result,
            "结果输入版本": version,
            "上一版结果版本": previous_result or _text(node.get("上一版结果版本")),
            "结果历史": history,
            "结果摘要": f"重试成功，基于 {version} 产出 {new_result}",
            "错误信息": "",
            "重试次数": int(node.get("重试次数") or 0) + 1,
            "待重试": False,
            "发布状态": "未发布",
            "发布版本": "",
            "最近执行时间": _now(),
        })
        node.pop("重试冲突版本", None)
        node.pop("重试冲突原因", None)
        note = f"按{priority}绑定输入版本 {version} 重试成功，结果版本 {new_result}"
        if previous_result:
            note += f"；上一版结果 {previous_result} 保留在版本历史"
        if latest and latest != version:
            note += f"；依赖图存在更新的完整输入版本 {latest}，本次未自动切换"
        self._close_pending_audits(script_no, node_id, note)
        self._append_audit(
            script_no=script_no,
            graph=graph,
            node=node,
            kind="节点重试",
            status=DONE_AUDIT,
            input_version=version,
            note=note,
            previous_version=previous_result,
            handle_note=(
                f"重试完成，结果输入版本 {version}"
                + (f"（上一版 {previous_input}）" if previous_input else "")
                + (f"；备注：{remark}" if remark else "")
                + (f"；操作人：{operator}" if operator else "")
            ),
        )
        return node, f"节点 {node_id} 已按输入版本 {version} 重试成功"

    def retry_audit(
        self,
        audit_id: int,
        *,
        input_version: str | None = None,
        remark: str | None = None,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        record = store.find_support(AUDIT_TABLE, audit_id)
        if record is None:
            return None, f"转换审计 {audit_id} 不存在"
        if _text(record.get("状态")) != PENDING_AUDIT:
            return None, f"转换审计 {record.get('审计编号')} 已处理，不能重复重试"
        script_no = _text(record.get("脚本编号"))
        version = _text(input_version) or _text(record.get("输入版本"))
        node, message = self.retry_node(
            script_no,
            _text(record.get("节点编号")),
            input_version=version or None,
            remark=remark,
            operator=operator,
        )
        if node is None:
            return None, message
        return node, message

    # ------------------------------------------------------------------ 发布
    def _publish_blockers(self, nodes: list[dict[str, Any]], node_ids: list[str]) -> list[str]:
        if not nodes:
            return ["转换数据集没有节点，不能发布"]
        blockers: list[str] = []
        failed = [
            _text(node.get("节点编号"))
            for node in nodes
            if self._node_status(node) != "成功" or node.get("待重试")
        ]
        if failed:
            blockers.append(f"存在未成功节点：{'、'.join(failed)}，失败节点保留上一版结果，不能发布")
        missing_result = [
            _text(node.get("节点编号")) for node in nodes if not _text(node.get("结果版本"))
        ]
        if missing_result:
            blockers.append(f"节点缺少可用结果：{'、'.join(missing_result)}")
        versions = {
            _text(node.get("结果输入版本"))
            for node in nodes
            if _text(node.get("结果版本"))
        }
        if len(versions) > 1:
            detail = "、".join(
                "{}={}".format(
                    _text(node.get("节点编号")),
                    _text(node.get("结果输入版本")) or "未知",
                )
                for node in nodes
            )
            blockers.append(f"节点结果输入版本不一致（{detail}），只能基于一致的依赖版本发布")
        elif versions:
            version = next(iter(versions))
            graph = _text(nodes[0].get("依赖图"))
            version_row = self._version(graph, version)
            missing = self._missing_nodes(version_row, node_ids)
            if version_row is None or not version_row.get("完整性") or missing:
                blockers.append(
                    f"输入版本 {version} 的依赖快照不完整"
                    + (f"，缺少 {'、'.join(missing)}" if missing else "")
                    + "，不能发布"
                )
            else:
                snapshot = version_row.get("节点快照") or {}
                wrong = [
                    f"{node_id}={snapshot.get(node_id)}"
                    for node_id in node_ids
                    if _text(snapshot.get(node_id)) != version
                ]
                if wrong:
                    blockers.append(f"节点绑定的输入版本与 {version} 不一致：{'、'.join(wrong)}")
        return blockers

    def publish(self, key: Any, *, input_version: str | None = None, operator: str | None = None) -> tuple[dict[str, Any] | None, str]:
        script = self._resolve_script(key)
        if script is None:
            return None, f"转换脚本 {key} 不存在或已归档"
        script_no = self._script_no(script)
        graph = _text(script.get("依赖编排"))
        nodes = self._nodes(script_no)
        node_ids = [_text(node.get("节点编号")) for node in nodes]
        blockers = self._publish_blockers(nodes, node_ids)
        requested = _text(input_version)
        if blockers:
            return None, "发布被拒绝：" + "；".join(blockers)
        versions = {_text(node.get("结果输入版本")) for node in nodes}
        version = next(iter(versions))
        if requested and requested != version:
            return None, (
                f"发布输入版本 {requested} 与节点结果输入版本 {version} 不一致，"
                "只能基于一致的依赖版本发布"
            )
        releases = store.support_rows(RELEASE_TABLE)
        for row in releases:
            if _text(row.get("脚本编号")) == script_no and _text(row.get("输入版本")) == version:
                return None, f"输入版本 {version} 已发布（发布编号 {row.get('发布编号')}），不需要重复发布"
        row = {
            "id": _next_id(releases),
            "发布编号": f"REL-{script_no}-{version}",
            "脚本编号": script_no,
            "依赖图": graph,
            "输入版本": version,
            "节点结果": {
                _text(node.get("节点编号")): _text(node.get("结果版本")) for node in nodes
            },
            "发布时间": _now(),
            "操作人": operator or "值班管理员",
        }
        releases.append(row)
        for node in nodes:
            node["发布状态"] = "已发布"
            node["发布版本"] = row["发布编号"]
        self._append_audit(
            script_no=script_no,
            graph=graph,
            node=nodes[0],
            kind="结果发布",
            status=DONE_AUDIT,
            input_version=version,
            note=f"全部 {len(nodes)} 个节点成功且输入版本一致，已发布 {row['发布编号']}",
            handle_note=f"发布输入版本 {version}",
        )
        return row, f"转换结果已按输入版本 {version} 发布，发布编号 {row['发布编号']}"
