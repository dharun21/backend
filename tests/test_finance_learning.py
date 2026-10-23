import unittest

from app.services.finance_learning import answer_finance_learning, answer_finance_workspace_limit


class FinanceLearningTests(unittest.TestCase):
    def test_cash_and_revenue_are_distinguished(self):
        answer = answer_finance_learning("What is the difference between cash received and revenue recorded?")
        self.assertIsNotNone(answer)
        self.assertIn("advance payment", answer.reply)
        self.assertIn("issue-date totals alone do not establish", answer.reply)
        self.assertTrue(any("ifrs.org" in source["url"] for source in answer.sources))

    def test_profit_working_capital_and_statements_do_not_claim_workspace_data(self):
        for question, required in (
            ("Explain profit versus cash available", "short of cash"),
            ("What is working capital?", "current assets - current liabilities"),
            ("Explain basic financial statements", "balance sheet"),
        ):
            with self.subTest(question=question):
                answer = answer_finance_learning(question)
                self.assertIsNotNone(answer)
                self.assertIn(required, answer.reply)

    def test_recordkeeping_links_primary_source_and_notes_jurisdiction(self):
        answer = answer_finance_learning("Why keep receipts and supporting documents?")
        self.assertIsNotNone(answer)
        self.assertIn("US businesses", answer.reply)
        self.assertEqual(answer.sources[0]["url"], "https://www.irs.gov/businesses/small-businesses-self-employed/recordkeeping")

    def test_receivable_payable_and_credit_note_boundaries(self):
        for question, required in (
            ("What are accounts receivable?", "partially paid"),
            ("What are accounts payable?", "disputed"),
            ("Explain credit notes and refunds", "not counted twice"),
        ):
            with self.subTest(question=question):
                answer = answer_finance_learning(question)
                self.assertIsNotNone(answer)
                self.assertIn(required, answer.reply)

    def test_break_even_rounds_up_to_whole_units(self):
        answer = answer_finance_learning("Calculate break even: fixed costs 1001, price per unit 10, variable cost per unit 6")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.intent, "finance_calculation")
        self.assertIn("251 whole units", answer.reply)
        self.assertIn("2,510.00", answer.reply)
        self.assertIn("not a calculation from confirmed workspace records", answer.assumptions[0])

    def test_break_even_rejects_nonpositive_contribution(self):
        answer = answer_finance_learning("Calculate break even: fixed costs 1000, price per unit 5, variable cost per unit 6")
        self.assertIsNotNone(answer)
        self.assertIn("No finite break-even", answer.reply)

    def test_margin_expense_ratio_and_monthly_change(self):
        cases = (
            ("Calculate gross margin with revenue 200 and COGS 120", "40.00%"),
            ("Calculate net margin with revenue 200 and net profit 30", "15.00%"),
            ("Calculate expense ratio with expenses 75 and revenue 300", "25.00%"),
            ("Calculate month-over-month change: previous month 100, current month 125", "25.00%"),
        )
        for question, expected in cases:
            with self.subTest(question=question):
                answer = answer_finance_learning(question)
                self.assertIsNotNone(answer)
                self.assertIn(expected, answer.reply)

    def test_zero_denominator_is_not_presented_as_zero_percent(self):
        for question in (
            "Calculate gross margin with revenue 0 and COGS 120",
            "Calculate month-over-month change: previous month 0, current month 125",
        ):
            with self.subTest(question=question):
                answer = answer_finance_learning(question)
                self.assertIsNotNone(answer)
                self.assertIn("undefined", answer.reply)

    def test_simple_totals_averages_and_percent_of(self):
        for question, expected in (
            ("Calculate total of 10, 20, 30", "60.00"),
            ("Calculate average of 10 and 20 and 30", "20.00"),
            ("What is 15% of 200?", "30.00"),
        ):
            with self.subTest(question=question):
                answer = answer_finance_learning(question)
                self.assertIsNotNone(answer)
                self.assertIn(expected, answer.reply)

    def test_account_specific_questions_do_not_use_generic_education(self):
        self.assertIsNone(answer_finance_learning("What is my cash flow?"))
        self.assertIsNone(answer_finance_learning("What are my overdue invoices?"))
        for question in ("What is my cash flow?", "What is my working capital?", "Show my disputed invoices"):
            with self.subTest(question=question):
                answer = answer_finance_workspace_limit(question)
                self.assertIsNotNone(answer)
                self.assertTrue(answer.needs_more_info)


if __name__ == "__main__":
    unittest.main()
