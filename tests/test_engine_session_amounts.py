import unittest

from src.core.engine import SignalEngine
from src.core.pipeline import GlobalGaleState
from src.core.recovery_profile import RecoveryProfile
from src.core.session_manager import SessionManager


class _DummyPocketClient:
    pass


class EngineSessionAmountsTests(unittest.TestCase):
    def test_session_amounts_apply_gale_multipliers(self):
        session = SessionManager(
            max_messages_per_session=6,
            target_profit_session=10.0,
            target_profit_per_win=5.0,
            stop_loss_count=3,
            payout=0.92,
        )
        recovery = RecoveryProfile(
            g1_mult=2.087,
            g2_mult=4.3556,
            max_trade_pct=0.10,
            max_total_exposure_pct=0.25,
        )

        engine = SignalEngine(
            pocket_client=_DummyPocketClient(),
            martingale_amounts=[1.0, 2.0, 3.0],
            martingale_mode="session",
            calc_payout_percent=92.0,
            calc_increment=2,
            calc_rule10_balance_threshold=50.0,
            calc_max_steps=3,
            result_grace_seconds=15,
            reference_utc_offset_hours=-3,
            color_output=False,
            signal_late_tolerance_seconds=300,
            global_gale_state=GlobalGaleState(profit_target=2.0),
            session_manager=session,
            recovery_profile=recovery,
            pocket_min_order_amount=1.0,
        )

        amounts = engine._session_amounts()

        self.assertEqual(amounts[0], 5.43)
        self.assertEqual(amounts[1], 11.33)
        self.assertEqual(amounts[2], 23.65)


if __name__ == "__main__":
    unittest.main()
