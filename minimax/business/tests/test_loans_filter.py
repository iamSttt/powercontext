"""目标功能测试：按状态筛选借阅记录（修改前应失败）。"""
from datetime import date

import pytest

from library.loans import Loan, list_loans


def make_loans() -> list[Loan]:
    return [
        Loan(3, 101, "alice", date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 10)),
        Loan(1, 102, "bob", date(2026, 9, 3), date(2026, 9, 17)),
        Loan(2, 103, "alice", date(2026, 9, 5), date(2026, 9, 19)),
    ]


def test_filter_borrowed():
    result = list_loans(make_loans(), status="borrowed")
    assert [loan.loan_id for loan in result] == [1, 2]


def test_filter_returned():
    result = list_loans(make_loans(), status="returned")
    assert [loan.loan_id for loan in result] == [3]


def test_filter_combined_with_borrower():
    result = list_loans(make_loans(), borrower="alice", status="borrowed")
    assert [loan.loan_id for loan in result] == [2]


def test_status_none_returns_all():
    result = list_loans(make_loans(), status=None)
    assert [loan.loan_id for loan in result] == [1, 2, 3]


def test_invalid_status_raises_value_error():
    with pytest.raises(ValueError):
        list_loans(make_loans(), status="lost")
