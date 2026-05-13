import unittest

from src.core.manual_operation_tracker import ManualOperationTracker
from src.core.pipeline import GlobalGaleState
from src.core.session_manager import SessionManager
from src.strategies.manual_strategies import MasanielloSessionState


class ManualOperationsIntegrationTests(unittest.TestCase):
    def test_manual_win_updates_all_states(self):
        gale = GlobalGaleState(profit_target=2.0)
        session = SessionManager(payout=0.92)
        manual = MasanielloSessionState(n_ops=6, w_needed=2, base_balance=10.0, payout_mult=1.92)

        tracker = ManualOperationTracker(
            global_gale_state=gale,
            session_manager=session,
            manual_strategy=manual,
        )

        tracker.register_manual_operation(
            asset="EURUSD OTC",
            side="BUY",
            amount=2.40,
            balance_before=98.80,
            balance_after=113.52,
            result="WIN",
            notes="test",
        )

        self.assertEqual(gale.current_step, 0)
        self.assertEqual(gale.accumulated_loss, 0.0)
        self.assertEqual(session.wins, 1)
        self.assertEqual(session.losses, 0)
        self.assertEqual(manual.wins, 1)
        self.assertEqual(manual.losses, 0)

    def test_manual_loss_updates_all_states(self):
        gale = GlobalGaleState(profit_target=2.0)
        session = SessionManager(payout=0.92)
        manual = MasanielloSessionState(n_ops=6, w_needed=2, base_balance=10.0, payout_mult=1.92)

        tracker = ManualOperationTracker(
            global_gale_state=gale,
            session_manager=session,
            manual_strategy=manual,
        )

        tracker.register_manual_operation(
            asset="EURUSD OTC",
            side="SELL",
            amount=3.00,
            balance_before=100.0,
            balance_after=97.0,
            result="LOSS",
            notes="test",
        )

        self.assertEqual(gale.current_step, 1)
        self.assertAlmostEqual(gale.accumulated_loss, 3.0, places=2)
        self.assertEqual(session.wins, 0)
        self.assertEqual(session.losses, 1)
        self.assertEqual(manual.wins, 0)
        self.assertEqual(manual.losses, 1)


if __name__ == "__main__":
    unittest.main()
