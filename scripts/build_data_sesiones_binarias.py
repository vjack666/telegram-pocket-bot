from pathlib import Path
import re
import pandas as pd
from datetime import datetime


def normalize_asset(asset: str) -> str:
    asset = re.sub(r"\s+OTC\s+OTC\b", " OTC", str(asset), flags=re.I)
    asset = re.sub(r"\s+", " ", asset).strip()
    return asset


def build_data_sesiones(
    repo_root: Path,
    md_rel: str = "ejemplo.md",
    backtest_rel: str = "runtime/backtest_masaniello_ejemplo.csv",
    output_rel: str = "runtime/Data_Sesiones_Binarias.csv",
) -> Path:
    md_path = repo_root / md_rel
    csv_path = repo_root / backtest_rel
    out_path = repo_root / output_rel

    text = md_path.read_text(encoding="utf-8")

    msg_re = re.compile(
        r"^\[(?P<stamp>[^\]]+)\]\s+(?P<channel>[^:]+):\s*(?P<body>.*?)(?=^\[[^\]]+\]\s+[^:]+:|\Z)",
        re.M | re.S,
    )
    asset_re = re.compile(r"💵\s*([^\n]+)")
    entry_re = re.compile(r"Entrada a las\s*(\d{2}:\d{2})")
    dir_re = re.compile(r"\b(ARRIBA|ABAJO)\b")

    result_rules = [
        ("Win Gale 2", re.compile(r"VICTORIA\s+EN\s+2(?:A|ª)?\s+MARTINGALA", re.I)),
        ("Win Gale", re.compile(r"VICTORIA\s+EN\s+1(?:A|ª)?\s+MARTINGALA", re.I)),
        ("Win Directo", re.compile(r"VICTORIA\s+DIRECTA", re.I)),
        ("Loss", re.compile(r"P[ÉE]RDIDA", re.I)),
    ]

    messages = []
    for match in msg_re.finditer(text):
        stamp = datetime.strptime(match.group("stamp"), "%d/%m/%Y %H:%M:%S")
        body = match.group("body").strip()
        messages.append({"stamp": stamp, "body": body})

    rows = []
    pending = None
    for msg in messages:
        body = msg["body"]
        if "INFORME DE OPERACIONES" in body or "LA SESIÓN VIP COMIENZA" in body:
            continue

        asset_match = asset_re.search(body)
        entry_match = entry_re.search(body)
        dir_match = dir_re.search(body)

        if asset_match and entry_match and dir_match:
            pending = {
                "Fecha": msg["stamp"].strftime("%Y-%m-%d"),
                "Hora": entry_match.group(1),
                "Activo": normalize_asset(asset_match.group(1)),
                "Direccion": dir_match.group(1),
                "Datetime": pd.to_datetime(msg["stamp"]),
            }
            continue

        if pending is None:
            continue

        matched = None
        for label, pattern in result_rules:
            if pattern.search(body):
                matched = label
                break

        if matched:
            rows.append({**pending, "Resultado": matched})
            pending = None

    md_df = pd.DataFrame(rows)
    md_df["Datetime"] = pd.to_datetime(md_df["Fecha"] + " " + md_df["Hora"], format="%Y-%m-%d %H:%M")
    md_df = md_df.sort_values("Datetime").reset_index(drop=True)

    sim_df = pd.read_csv(csv_path)
    sim_df = sim_df.rename(
        columns={
            "Hora de Entrada": "Hora",
            "Activo (Asset)": "Activo",
            "Direccion (ARRIBA/ABAJO)": "Direccion",
            "Resultado del Backtest": "Resultado",
            "Ciclo Masaniello": "Ciclo_Masaniello",
        }
    )
    sim_df["Datetime"] = pd.to_datetime(sim_df["Fecha"] + " " + sim_df["Hora"], format="%Y-%m-%d %H:%M")
    sim_df["Activo"] = sim_df["Activo"].map(normalize_asset)
    sim_df = sim_df[["Fecha", "Hora", "Activo", "Direccion", "Resultado", "Datetime", "Ciclo_Masaniello"]]

    merged = md_df.merge(
        sim_df,
        on=["Fecha", "Hora", "Activo", "Direccion", "Resultado", "Datetime"],
        how="left",
    )

    if merged["Ciclo_Masaniello"].isna().any():
        fallback = md_df.merge(
            sim_df.drop(columns=["Direccion", "Datetime"]),
            on=["Fecha", "Hora", "Activo", "Resultado"],
            how="left",
        )
        mask = merged["Ciclo_Masaniello"].isna()
        merged.loc[mask, "Ciclo_Masaniello"] = fallback.loc[mask, "Ciclo_Masaniello"]

    merged = merged.sort_values("Datetime").reset_index(drop=True)
    merged["Sesion_Ordinal"] = (merged.index // 6) + 1
    merged["ID_Sesion"] = merged["Sesion_Ordinal"].map(lambda value: f"S-{value:03d}")

    merged["ITM"] = merged["Resultado"].isin(["Win Directo", "Win Gale", "Win Gale 2"])
    step_map = {"Win Directo": 0, "Win Gale": 1, "Win Gale 2": 2, "Loss": 2}
    merged["Paso_Alcanzado"] = merged["Resultado"].map(step_map).fillna(0).astype(int)

    merged["Wins_en_Sesion"] = merged.groupby("ID_Sesion")["ITM"].cumsum().astype(int)
    merged["Mensajes_en_Sesion"] = merged.groupby("ID_Sesion").cumcount() + 1

    merged["Objetivo_Cumplido"] = merged["Wins_en_Sesion"] >= 2
    merged["Estado_Sesion"] = merged["Objetivo_Cumplido"].map(lambda ok: "Exitosa" if ok else "En Progreso")

    exposure_values = []
    for _, group in merged.groupby("ID_Sesion", sort=False):
        goal_idx = group.index[group["Wins_en_Sesion"] >= 2]
        cut_idx = goal_idx[0] if len(goal_idx) > 0 else group.index[-1]
        max_exposure = int(merged.loc[group.index[0]:cut_idx, "Paso_Alcanzado"].max())
        exposure_values.extend([max_exposure] * len(group))
    merged["Exposicion_Max"] = exposure_values

    wins_final = merged.groupby("ID_Sesion")["ITM"].sum().astype(int).rename("Wins_Finales_Sesion")
    merged = merged.merge(wins_final, on="ID_Sesion", how="left")

    output_columns = [
        "Fecha",
        "Hora",
        "Activo",
        "Direccion",
        "Resultado",
        "ID_Sesion",
        "Mensajes_en_Sesion",
        "Wins_en_Sesion",
        "Wins_Finales_Sesion",
        "Objetivo_Cumplido",
        "Estado_Sesion",
        "Exposicion_Max",
        "Ciclo_Masaniello",
    ]

    out_df = merged[output_columns].copy()
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")

    match_pct = round(100.0 * float(out_df["Ciclo_Masaniello"].notna().mean()), 2)
    print(
        {
            "output": str(out_path),
            "rows": int(len(out_df)),
            "sesiones": int(out_df["ID_Sesion"].nunique()),
            "match_ciclo_pct": match_pct,
        }
    )

    return out_path


if __name__ == "__main__":
    build_data_sesiones(Path(__file__).resolve().parents[1])
