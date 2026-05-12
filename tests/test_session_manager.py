import unittest

from src.core.session_manager import SessionManager


class SessionManagerTests(unittest.TestCase):
    def test_stop_loss_three_losses_resets_session(self):
        sm = SessionManager(
            max_messages_per_session=6,
            target_profit_session=10.0,
            target_profit_per_win=5.0,
            stop_loss_count=3,
            payout=0.92,
        )

        stake_1 = sm.current_entry_stake()
        self.assertAlmostEqual(stake_1, 5.43, places=2)

        debt_1 = stake_1
        sm.record_loss(amount=debt_1)
        stake_2 = sm.current_entry_stake()
        self.assertAlmostEqual(stake_2, 11.34, places=2)

        debt_2 = debt_1 + stake_2
        sm.record_loss(amount=debt_2)
        stake_3 = sm.current_entry_stake()
        self.assertAlmostEqual(stake_3, 23.66, places=2)

        debt_3 = debt_2 + stake_3
        self.assertAlmostEqual(debt_3, 40.43, places=1)
        sm.record_loss(amount=debt_3)

        self.assertEqual(sm.sessions_lost, 1)
        self.assertEqual(sm.last_close_reason, "stop_loss_3_losses")
        self.assertEqual(sm.messages_in_session, 0)
        self.assertEqual(sm.wins, 0)
        self.assertEqual(sm.losses, 0)
        self.assertEqual(sm.accumulated_loss, 0.0)

    def test_take_profit_two_wins_resets_session(self):
        sm = SessionManager(payout=0.92)
        sm.record_win()
        sm.record_win()

        self.assertEqual(sm.sessions_won, 1)
        self.assertEqual(sm.last_close_reason, "take_profit_2_wins")
        self.assertEqual(sm.messages_in_session, 0)
        self.assertEqual(sm.accumulated_loss, 0.0)


if __name__ == "__main__":
    unittest.main()
