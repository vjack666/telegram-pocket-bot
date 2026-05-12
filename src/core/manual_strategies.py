from __future__ import annotations

import logging


class MasanielloSessionState:
    """Estrategia Masaniello mantenida solo para flujos manuales/legacy."""

    def __init__(self, n_ops=12, w_needed=4, base_balance=300.0, payout_mult=1.92, max_losses=3):
        self.n_ops = max(1, n_ops)
        self.w_needed = max(1, w_needed)
        self.base_balance = max(1.0, base_balance)
        self.payout_mult = max(1.01, payout_mult)
        self.max_losses = max(1, max_losses)
        self.wins = 0
        self.losses = 0
        self.session_blocked = False
        self.result_history = []
        self.blocks_won_today = 0
        self.blocks_lost_today = 0
        self.global_stop = False
        self.state_change_callback = None

    @property
    def signals_consumed(self):
        return self.wins + self.losses

    @property
    def is_session_over(self):
        return self.wins >= self.w_needed or self.signals_consumed >= self.n_ops

    def print_status(self):
        print("\n==============================")
        print(" PROYECCION MASANIELLO (5/2)")
        print(" Caja inicial: $%.2f" % self.base_balance)
        print(" Payout: %.2f" % ((self.payout_mult - 1) * 100))
        print("------------------------------")
        saldo = self.base_balance
        wins = 0
        losses = 0
        for i in range(1, self.n_ops + 1):
            stake = self.masaniello_stake(saldo, losses, wins)
            print(" Paso %d | Stake: $%.2f | Saldo: $%.2f | W: %d | L: %d" % (i, stake, saldo, wins, losses))
            if i == 1 or i == 3:
                saldo -= stake
                losses += 1
            else:
                saldo += stake * (self.payout_mult - 1)
                wins += 1
        print("------------------------------")
        objetivo = self.base_balance * (1 + self.payout_mult ** self.w_needed)
        print(" Objetivo teorico: $%.2f" % objetivo)
        print("[SESION]: Bloque %d/3" % (self.blocks_won_today + self.blocks_lost_today + 1))
        print("[PROGRESO]: W: %d | L: %d (Objetivo: %d ITM)" % (self.wins, self.losses, self.w_needed))
        next_stake = self.masaniello_stake(self.base_balance, self.losses, self.wins)
        print("[PROYECCION]: Siguiente Stake: $%.2f | Saldo Objetivo Final: $%.2f" % (next_stake, objetivo))
        print("==============================\n")

    def current_entry_stake(self):
        return self.masaniello_stake(self.base_balance, self.losses, self.wins)

    def peek_next_stake_if_loss(self):
        return self.masaniello_stake(self.base_balance, self.losses + 1, self.wins)

    def set_state_change_callback(self, callback):
        self.state_change_callback = callback

    def to_dict(self):
        return {
            "wins": self.wins,
            "losses": self.losses,
            "signals_consumed": self.signals_consumed,
            "is_session_blocked": self.session_blocked,
            "result_history": self.result_history,
            "n_ops": self.n_ops,
            "w_needed": self.w_needed,
            "max_losses": self.max_losses,
            "base_balance": self.base_balance,
            "payout_mult": self.payout_mult,
            "blocks_won_today": self.blocks_won_today,
            "blocks_lost_today": self.blocks_lost_today,
            "global_stop": self.global_stop,
        }

    def restore_state(self, wins, losses, session_blocked, result_history=None, base_balance=None, payout_mult=None, blocks_won_today=None, blocks_lost_today=None, global_stop=None, notify=True):
        self.wins = max(0, int(wins))
        self.losses = max(0, int(losses))
        self.session_blocked = bool(session_blocked)
        history = result_history or []
        self.result_history = [str(item) for item in history if str(item) in {"W", "L"}][-200:]
        if base_balance is not None:
            self.base_balance = max(1.0, float(base_balance))
        if payout_mult is not None:
            self.payout_mult = max(1.01, float(payout_mult))
        if blocks_won_today is not None:
            self.blocks_won_today = max(0, int(blocks_won_today))
        if blocks_lost_today is not None:
            self.blocks_lost_today = max(0, int(blocks_lost_today))
        if global_stop is not None:
            self.global_stop = bool(global_stop)
        if notify:
            self._notify_state_change("restore_state")

    def reset_session(self, reason="manual_reset", notify=True):
        self.wins = 0
        self.losses = 0
        self.session_blocked = False
        self.result_history = []
        if notify:
            self._notify_state_change(f"reset:{reason}")

    def reset_daily_counters(self, notify=True):
        self.blocks_won_today = 0
        self.blocks_lost_today = 0
        self.global_stop = False
        if notify:
            self._notify_state_change("reset:daily_counters")

    def _notify_state_change(self, reason):
        if self.state_change_callback is None:
            return
        try:
            self.state_change_callback(self, reason)
        except Exception:
            logging.exception("MasanielloSession callback fallo (reason=%s)", reason)

    def record_win(self):
        if self.global_stop:
            self._notify_state_change("record_win_ignored_global_stop")
            return
        self.wins += 1
        self.session_blocked = False
        self.result_history.append("W")
        self.result_history = self.result_history[-200:]
        if self.is_session_over:
            self._complete_block(won=(self.wins >= self.w_needed), reason="record_win_session_over")
            return
        self._notify_state_change("record_win")

    def record_loss(self, _amount: float | None = None):
        if self.global_stop:
            self._notify_state_change("record_loss_ignored_global_stop")
            return
        self.losses += 1
        self.result_history.append("L")
        self.result_history = self.result_history[-200:]
        if self.is_session_over:
            self._complete_block(won=(self.wins >= self.w_needed), reason="record_loss_session_over")
            return
        self._notify_state_change("record_loss")

    def _complete_block(self, won, reason):
        if won:
            self.blocks_won_today += 1
        else:
            self.blocks_lost_today += 1
            if self.blocks_lost_today >= 3:
                self.global_stop = True
                self.session_blocked = True

        self.wins = 0
        self.losses = 0
        self.result_history = []
        if not self.global_stop:
            self.session_blocked = False

        self._notify_state_change(f"{reason}:{'block_win' if won else 'block_loss'}")

    def update_base(self, new_base):
        self.base_balance = max(1.0, float(new_base))

    def update_payout_mult(self, payout_mult):
        self.payout_mult = max(1.01, float(payout_mult))

    def masaniello_stake(self, balance, losses_so_far, wins_so_far):
        n = self.n_ops
        w = self.w_needed
        pm = self.payout_mult
        objetivo = balance * (1 + pm ** w)
        ops_left = n - (losses_so_far + wins_so_far)
        wins_left = w - wins_so_far
        if ops_left <= 0 or wins_left <= 0 or wins_left > ops_left:
            return 0.01
        p_win_fwd = self.forward_prob(ops_left - 1, wins_left - 1, pm)
        p_lose_fwd = self.forward_prob(ops_left - 1, wins_left, pm)
        denom = p_win_fwd + (pm - 1) * p_lose_fwd
        if denom == 0:
            return balance
        stake = balance * (1 - pm * p_win_fwd / denom)
        return round(max(0.01, min(stake, balance)), 2)

    @staticmethod
    def forward_prob(ops_left, wins_needed, payout_mult):
        if wins_needed <= 0:
            return 1.0
        if wins_needed > ops_left:
            return 0.0
        if wins_needed == ops_left:
            return payout_mult ** ops_left
        p_win = MasanielloSessionState.forward_prob(ops_left - 1, wins_needed - 1, payout_mult)
        p_lose = MasanielloSessionState.forward_prob(ops_left - 1, wins_needed, payout_mult)
        denom = p_win + (payout_mult - 1) * p_lose
        if denom == 0:
            return 0.0
        return payout_mult * p_win * p_lose / denom
