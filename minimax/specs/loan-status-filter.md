# 规格：借阅查询支持按状态筛选

- 规格编号：SPEC-LOAN-002
- 状态：待实现（本次开发任务）
- 关联代码：`business/library/loans.py` → `list_loans`
- 关联测试：`business/tests/test_loans_filter.py`

## 背景

借阅列表目前只能按借阅人过滤，管理台需要快速区分「在借」与「已归还」的记录。

## 需求

为 `list_loans` 增加可选参数 `status`：

```python
def list_loans(
    loans: list[Loan],
    borrower: str | None = None,
    status: str | None = None,
) -> list[Loan]:
```

## 验收标准

1. `status=None` 时行为与现状完全一致（返回全部，按 `loan_id` 升序）。
2. `status="borrowed"` 时只返回 `returned_on is None` 的记录。
3. `status="returned"` 时只返回 `returned_on is not None` 的记录。
4. `borrower` 与 `status` 可以同时使用，两个条件取交集。
5. 传入其他 status 值（如 `"lost"`）时抛出 `ValueError`，异常信息包含 `invalid status` 与该值。
6. 返回值仍然按 `loan_id` 升序。
7. 不得改变 `Loan` 数据类、`is_overdue`、`summarize` 的行为。

## 非目标

- 不引入数据库或外部查询。
- 不改变函数名、参数顺序中 `borrower` 的位置。

## 变更记录

## 变更记录

- 2026-10-27：补充验收标准 8——非法 status 的异常信息必须包含该值本身（例如 `invalid status: lost`）。
