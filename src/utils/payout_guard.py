"""payout_guard.py
Verifica si el payout actual es rentable.
- En modo automático: log + retorna False para que el engine omita la señal.
- En modo manual: abre un popup tkinter bloqueante (siempre por encima de todo)
  que no desaparece hasta que el usuario pulsa OK.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional


_PAYOUT_MIN_DEFAULT = 0.80


def is_profitable(payout_percent: float, min_profitable: float = _PAYOUT_MIN_DEFAULT) -> bool:
    """Devuelve True si el payout justifica la operación."""
    return float(payout_percent) / 100.0 >= float(min_profitable)


def _show_blocking_popup(asset: str, payout_percent: float, min_profitable_pct: float) -> None:
    """Abre ventana tkinter en hilo separado y bloquea hasta que el usuario pulse OK."""
    import tkinter as tk
    from tkinter import ttk

    done = threading.Event()

    def _build_and_run() -> None:
        root = tk.Tk()
        root.withdraw()  # ocultar ventana raíz

        win = tk.Toplevel(root)
        win.title("⚠️  Señal NO rentable")
        win.resizable(False, False)

        # Siempre al frente, sin poder minimizarse sin el OK
        win.attributes("-topmost", True)
        win.grab_set()
        win.focus_force()

        # Centrar en pantalla
        win.update_idletasks()
        w, h = 420, 200
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="⚠️  SEÑAL OMITIDA — PAYOUT NO RENTABLE",
            font=("Arial", 12, "bold"),
        ).pack(pady=(0, 10))

        ttk.Label(
            frame,
            text=f"Par: {asset}\nPayout actual: {payout_percent:.1f}%\nMínimo rentable: {min_profitable_pct:.0f}%",
            justify="center",
        ).pack()

        ttk.Label(
            frame,
            text="El sistema esperará la siguiente señal.",
            foreground="gray",
        ).pack(pady=(6, 12))

        def _on_ok() -> None:
            done.set()
            win.destroy()
            root.destroy()

        # Botón OK grande y centrado
        btn = ttk.Button(frame, text="  OK — Entendido  ", command=_on_ok)
        btn.pack()
        btn.focus_set()

        # Interceptar cierre con X para forzar el OK
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        root.mainloop()

    t = threading.Thread(target=_build_and_run, daemon=True)
    t.start()
    done.wait()  # bloquea hasta que el usuario pulse OK


async def check_payout_or_notify(
    asset: str,
    payout_percent: float,
    min_profitable: float,
    is_manual_mode: bool,
) -> bool:
    """
    Verifica si el payout es rentable.
    - Retorna True  → operación permitida.
    - Retorna False → operación bloqueada (log + popup en manual).
    """
    if is_profitable(payout_percent, min_profitable):
        return True

    min_pct = float(min_profitable) * 100.0
    logging.warning(
        "⛔ Señal OMITIDA por payout insuficiente: asset=%s payout=%.1f%% minimo=%.0f%%",
        asset,
        float(payout_percent),
        min_pct,
    )

    if is_manual_mode:
        # Abrir popup bloqueante en executor para no bloquear el event loop
        await asyncio.get_event_loop().run_in_executor(
            None,
            _show_blocking_popup,
            asset,
            float(payout_percent),
            min_pct,
        )

    return False
