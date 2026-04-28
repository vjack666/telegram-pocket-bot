import os
import re
import shutil
from datetime import datetime, timezone


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GREY = "\033[37m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _w() -> int:
    return max(80, shutil.get_terminal_size((80, 24)).columns)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _paint(text: str, *codes: str) -> str:
    return "".join(codes) + text + C.RESET


def _hr(char: str = "-") -> str:
    return _paint(char * _w(), C.DIM, C.GREY)


def _side_badge(side: str) -> str:
    s = (side or "").upper().strip()
    if s in {"BUY", "CALL", "UP"}:
        return _paint(f" ^ {s} ", C.BOLD, C.GREEN)
    return _paint(f" v {s} ", C.BOLD, C.RED)


def _semaphore_badge(state: str) -> str:
    if "VERDE" in state:
        return _paint("o LISTO  ", C.BOLD, C.GREEN)
    if "AMARILLO" in state:
        return _paint("o PREP   ", C.BOLD, C.YELLOW)
    return _paint("o ESPERA ", C.DIM, C.RED)


def _fmt_time(hh: int, mm: int, ss: int) -> str:
    return _paint(f"{hh:02d}:{mm:02d}:{ss:02d}", C.BOLD, C.WHITE)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def clear_countdown_line() -> None:
    print("\r" + " " * _w() + "\r", end="", flush=True)


def print_signal_summary(
    asset: str,
    side: str,
    expiry_minutes: int,
    martingale_mode: str,
    amounts: list[float],
    schedule_labels: list[str],
    color_output: bool = True,
) -> None:
    clear_screen()

    if color_output:
        title = _paint("  BOT POCKET OPTION - SENAL RECIBIDA  ", C.BOLD, C.CYAN)
        amounts_str = "  ->  ".join(
            _paint(f"${v:.2f}", C.BOLD, C.WHITE if i == 0 else C.MAGENTA)
            for i, v in enumerate(amounts)
        )
    else:
        title = "  BOT POCKET OPTION - SENAL RECIBIDA  "
        amounts_str = "  ->  ".join(f"${v:.2f}" for v in amounts)

    step_labels = ["Entrada"] + [f"Martingala {i}" for i in range(1, len(schedule_labels))]
    schedule_lines: list[str] = []
    for step, time_label in zip(step_labels, schedule_labels):
        if color_output:
            color = C.GREY if "Martingala" in step else C.WHITE
            schedule_lines.append(
                f"  {_paint(step + ':', C.DIM, C.GREY):<26}{_paint(time_label, color)}"
            )
        else:
            schedule_lines.append(f"  {step + ':':<18}{time_label}")

    if color_output:
        lines = [
            _hr("="),
            title,
            _hr("-"),
            "",
            f"  {_paint('Par:', C.DIM, C.GREY):<20}{_paint(asset, C.BOLD, C.CYAN)}   {_side_badge(side)}",
            f"  {_paint('Expiracion:', C.DIM, C.GREY):<20}{_paint(str(expiry_minutes) + ' min', C.WHITE)}",
            f"  {_paint('Modo martingala:', C.DIM, C.GREY):<20}{_paint(martingale_mode, C.YELLOW)}",
            f"  {_paint('Montos:', C.DIM, C.GREY):<20}{amounts_str}",
            "",
            f"  {_paint('Horarios:', C.DIM, C.GREY)}",
            *schedule_lines,
            "",
            _hr("-"),
        ]
    else:
        lines = [
            "=" * _w(),
            title,
            "-" * _w(),
            "",
            f"  Par:            {asset}  {side}",
            f"  Expiracion:     {expiry_minutes} min",
            f"  Modo:           {martingale_mode}",
            f"  Montos:         {amounts_str}",
            "",
            "  Horarios:",
            *schedule_lines,
            "",
            "-" * _w(),
        ]

    print("\n".join(lines))


def print_countdown_line(
    step_name: str,
    asset: str,
    side: str,
    amount: float,
    hh: int,
    mm: int,
    ss: int,
    semaphore: str,
    color_output: bool = True,
) -> None:
    if color_output:
        step_col = _paint(f"[{step_name}]", C.BOLD, C.YELLOW)
        asset_col = _paint(asset, C.CYAN)
        side_col = _paint(side, C.GREEN if side in {"BUY", "CALL", "UP"} else C.RED)
        amt_col = _paint(f"${amount:.2f}", C.WHITE)
        timer_col = _fmt_time(hh, mm, ss)
        sem_col = _semaphore_badge(semaphore)
        line = f"\r  {step_col} {asset_col} {side_col}  {amt_col}  t {timer_col}  {sem_col}  "
    else:
        line = (
            f"\r  [{step_name}] {asset} {side}  ${amount:.2f}"
            f"  {hh:02d}:{mm:02d}:{ss:02d}  {semaphore}  "
        )

    pad = max(0, _w() - len(_strip_ansi(line)) - 1)
    print(line + " " * pad, end="", flush=True)


def print_order_event(
    event: str,
    step_name: str,
    asset: str,
    side: str,
    amount: float,
    extra: str = "",
    color_output: bool = True,
) -> None:
    clear_countdown_line()

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    if event == "executed":
        symbol = "OK"
        color = C.BLUE
        msg = f"Orden EJECUTADA  [{step_name}]  {asset} {side}  ${amount:.2f}"
    elif event == "error":
        symbol = "ERR"
        color = C.RED
        msg = f"Orden FALLIDA    [{step_name}]  {asset} {side}  ${amount:.2f}  {extra}"
    elif event == "win":
        symbol = "WIN"
        color = C.GREEN
        msg = f"Resultado WIN en {step_name}. {extra}".strip()
    elif event == "loss":
        symbol = "LOSS"
        color = C.RED
        msg = f"Resultado LOSS final - esperando nueva senal. {extra}".strip()
    else:
        symbol = "INFO"
        color = C.GREY
        msg = f"[{step_name}] {extra}".strip()

    if color_output:
        ts_str = _paint(f"[{ts} UTC]", C.DIM, C.GREY)
        sym_str = _paint(symbol, C.BOLD, color)
        msg_str = _paint(msg, color)
        print(f"  {ts_str} {sym_str}  {msg_str}")
    else:
        print(f"  [{ts} UTC] {symbol}  {msg}")

    print(_hr("-"))
