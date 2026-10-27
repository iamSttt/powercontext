"""图书借阅核心逻辑（SDD 示例业务工程）。

本模块提供借阅记录的查询、逾期判断与统计。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Loan:
    """一条借阅记录。"""

    loan_id: int
    book_id: int
    borrower: str
    borrowed_on: date
    due_on: date
    returned_on: date | None = None

    @property
    def status(self) -> str:
        return "returned" if self.returned_on is not None else "borrowed"


def list_loans(loans: list[Loan], borrower: str | None = None) -> list[Loan]:
    """返回借阅记录，按 loan_id 升序。

    borrower 不为 None 时，只返回该借阅人的记录。
    """
    result = list(loans)
    if borrower is not None:
        result = [loan for loan in result if loan.borrower == borrower]
    return sorted(result, key=lambda loan: loan.loan_id)


def is_overdue(loan: Loan, today: date | None = None) -> bool:
    """判断一条借阅是否逾期。

    已归还的记录：以归还时间是否晚于应还时间为准。
    未归还的记录：以今天是否晚于应还时间为准。
    """
    if loan.returned_on is not None:
        return loan.returned_on > loan.due_on
    today = today or date.today()
    return today > loan.due_on


def summarize(loans: list[Loan]) -> dict[str, int]:
    """统计借阅记录：总数、在借数、已还数、逾期数。"""
    total = len(loans)
    returned = sum(1 for loan in loans if loan.status == "returned")
    borrowed = total - returned
    overdue = sum(1 for loan in loans if is_overdue(loan))
    return {
        "total": total,
        "borrowed": borrowed,
        "returned": returned,
        "overdue": overdue,
    }
