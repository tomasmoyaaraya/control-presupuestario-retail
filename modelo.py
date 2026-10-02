"""Fase 2a - Carga los CSV en una base SQLite con esquema estrella y crea las vistas de sql/vistas.sql.

Uso:  python modelo.py   ->  valdora.db
"""
import sqlite3
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
DATOS = BASE / "datos"
DB = BASE / "valdora.db"


def cargar(con: sqlite3.Connection):
    tiendas = pd.read_csv(DATOS / "tiendas.csv")
    categorias = pd.read_csv(DATOS / "categorias.csv")
    ventas = pd.read_csv(DATOS / "ventas_diarias.csv")
    ppto = pd.read_csv(DATOS / "presupuesto_ventas.csv")
    g_real = pd.read_csv(DATOS / "gastos_real.csv").assign(escenario="real")
    g_ppto = pd.read_csv(DATOS / "gastos_presupuesto.csv").assign(escenario="ppto")

    fechas = pd.to_datetime(pd.Series(sorted(ventas.fecha.unique())))
    dim_fecha = pd.DataFrame({
        "fecha": fechas.dt.strftime("%Y-%m-%d"), "año": fechas.dt.year, "mes": fechas.dt.month,
        "dia": fechas.dt.day, "dia_semana": fechas.dt.dayofweek,  # 0 = lunes
        "periodo": fechas.dt.strftime("%Y-%m"),
    })

    tablas = {
        "dim_tienda": tiendas, "dim_categoria": categorias, "dim_fecha": dim_fecha,
        "fact_ventas": ventas, "fact_presupuesto": ppto,
        "fact_gastos": pd.concat([g_real, g_ppto], ignore_index=True),
    }
    for nombre, df in tablas.items():
        df.to_sql(nombre, con, if_exists="replace", index=False)

    con.executescript("""
        CREATE INDEX IF NOT EXISTS ix_ventas ON fact_ventas(fecha, tienda_id, categoria_id);
        CREATE INDEX IF NOT EXISTS ix_ppto ON fact_presupuesto(año, mes, tienda_id, categoria_id);
        CREATE INDEX IF NOT EXISTS ix_gastos ON fact_gastos(año, mes, tienda_id, cuenta, escenario);
        CREATE INDEX IF NOT EXISTS ix_fecha ON dim_fecha(fecha);
    """)
    con.executescript((BASE / "sql" / "vistas.sql").read_text(encoding="utf-8"))
    return {n: len(df) for n, df in tablas.items()}


if __name__ == "__main__":
    DB.unlink(missing_ok=True)
    with sqlite3.connect(DB) as con:
        for tabla, n in cargar(con).items():
            print(f"  {tabla:<18} {n:>8,} filas")
        vistas = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")]
        print("  vistas:", ", ".join(vistas))
    print(f"Base lista: {DB}")
