"""回归测试：现有借阅行为必须保持不变。"""
from datetime import date

from library.loans import Loan, is_overdue, list_loans, summarize


def make_loans() -> list[Loan]:
    return [
        Loan(3, 101, "alice", date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 10)),
        Loan(1, 102, "bob", date(2026, 9, 3), date(2026, 9, 17)),
        Loan(2, 103, "alice", date(2026, 9, 5), date(2026, 9, 19)),
    ]


def test_list_all_sorted_by_loan_id():
    loans = make_loans()
    result = list_loans(loans)
    assert [loan.loan_id for loan in result] == [1, 2, 3]


def test_filter_by_borrower():
    loans = make_loans()
    result = list_loans(loans, borrower="alice")
    assert [loan.loan_id for loan in result] == [2, 3]


def test_filter_unknown_borrower_returns_empty():
    assert list_loans(make_loans(), borrower="nobody") == []


def test_is_overdue_for_returned_and_unreturned():
    loans = {loan.loan_id: loan for loan in make_loans()}
    # 已归还且按时归还
    assert is_overdue(loans[3]) is False
    # 未归还，应还日早于今天
    assert is_overdue(loans[1], today=date(2026, 9, 20)) is True
    # 未归还，尚未到期
    assert is_overdue(loans[2], today=date(2026, 9, 10)) is False


def test_summarize_counts():
    stats = summarize(make_loans())
    assert stats["total"] == 3
    assert stats["borrowed"] == 2
    assert stats["returned"] == 1
