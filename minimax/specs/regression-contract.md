# 回归契约：现有借阅行为必须保持

- 规格编号：SPEC-REG-001
- 状态：长期有效
- 关联测试：`business/tests/test_loans_basic.py`

## 必须保持的行为

1. `Loan` 数据类字段不变：`loan_id, book_id, borrower, borrowed_on, due_on, returned_on`。
2. `Loan.status` 属性：`returned_on` 非空返回 `"returned"`，否则返回 `"borrowed"`。
3. `list_loans(loans)` 默认返回全部记录，按 `loan_id` 升序。
4. `list_loans(loans, borrower="alice")` 只返回 alice 的记录；未知借阅人返回空列表。
5. `is_overdue`：
   - 已归还：`returned_on > due_on` 为逾期；
   - 未归还：`today > due_on` 为逾期。
6. `summarize` 返回 `total/borrowed/returned/overdue` 四个键，计数口径不变。
7. 函数不得就地修改传入的 `loans` 列表。

## 验证方式

修改后必须运行：

```bash
pytest business/tests/test_loans_basic.py -q
```

全部通过才算满足回归契约。
