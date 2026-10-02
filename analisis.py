"""Fase 2b - Análisis de control de gestión sobre valdora.db.

Uso:  python analisis.py   ->  resultados/informe_control_gestion.xlsx

1. KPIs del año a la fecha (meses cerrados) vs presupuesto
2. Puente de margen: presupuesto -> real, separado en precio, costo, volumen, mix de categorías y mix de tiendas
3. Proyección de cierre del mes en curso (y su error medido en meses pasados)
4. Alertas automáticas con reglas fijadas de antemano
5. Verificación: ¿las alertas encontraron las anomalías plantadas?
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
DB = BASE / "valdora.db"
OUT = BASE / "resultados"
MM = 1e6

# Reglas de alerta (fijadas antes de mirar los resultados; umbrales estándar)
R_GASTO_DESV = 0.20      # gasto real > presupuesto en más de 20%...
R_GASTO_Z = 3.0          # ...y atípico frente al resto de las tiendas (z-score robusto > 3)
R_VENTA_CUMPL = 0.92     # cumplimiento de venta zona x categoría bajo 92%...
R_VENTA_MESES = 3        # ...3 meses seguidos
R_PRECIO_DESV = -0.05    # precio real 5% bajo presupuesto...
R_MARGEN_PP = -0.02      # ...o margen 2 puntos bajo presupuesto...
R_PRECIO_MESES = 2       # ...2 meses seguidos
RECURRENTE = (2, 3)      # alerta recurrente: se repite en 2+ de los últimos 3 meses
CUENTAS_VARIABLES = ["Comisiones", "Mermas", "Insumos y otros"]
CORTES_PROYECCION = [10, 15, 20]


def leer(con, sql):
    return pd.read_sql(sql, con)


# ------------------------------------------------------------------
#  Periodo
# ------------------------------------------------------------------
def periodo_actual(con):
    ult = pd.Timestamp(leer(con, "SELECT MAX(fecha) f FROM dim_fecha").f[0])
    fin_mes = ult + pd.offsets.MonthEnd(0)
    return ult, fin_mes.day, ult.day == fin_mes.day


# ------------------------------------------------------------------
#  1. KPIs
# ------------------------------------------------------------------
def kpis(eerr: pd.DataFrame, año: int, meses: list) -> dict:
    d = eerr[(eerr.año == año) & eerr.mes.isin(meses)]
    s = d[["venta_real", "venta_ppto", "margen_real", "margen_ppto", "gastos_real", "gastos_ppto",
           "ebitda_real", "ebitda_ppto"]].sum()
    return {
        "Venta real (MM$)": s.venta_real / MM, "Venta ppto (MM$)": s.venta_ppto / MM,
        "Cumplimiento venta %": s.venta_real / s.venta_ppto * 100,
        "Margen bruto real %": s.margen_real / s.venta_real * 100,
        "Margen bruto ppto %": s.margen_ppto / s.venta_ppto * 100,
        "Gastos real (MM$)": s.gastos_real / MM, "Gastos ppto (MM$)": s.gastos_ppto / MM,
        "EBITDA real (MM$)": s.ebitda_real / MM, "EBITDA ppto (MM$)": s.ebitda_ppto / MM,
        "Cumplimiento EBITDA %": s.ebitda_real / s.ebitda_ppto * 100,
        "EBITDA real % venta": s.ebitda_real / s.venta_real * 100,
    }


# ------------------------------------------------------------------
#  2. Puente de margen (precio / costo / volumen / mix)
# ------------------------------------------------------------------
def puente_margen(rvp: pd.DataFrame) -> pd.Series:
    """Descompone margen_real - margen_ppto. Suma exacta (cuadra al peso).

    Nivel de celda: tienda x categoría x mes. Unidades comparables solo dentro de una categoría,
    por eso el mix se separa en dos niveles: entre categorías y entre tiendas dentro de la categoría.
    """
    d = rvp.copy()
    d = d[(d.unidades_real > 0) | (d.unidades_ppto > 0)]
    qr, qp = d.unidades_real, d.unidades_ppto
    pr = np.where(qr > 0, d.venta_real / qr.replace(0, np.nan), 0)
    pp = np.where(qp > 0, d.venta_ppto / qp.replace(0, np.nan), pr)       # sin ppto: sin efecto precio
    cr = np.where(qr > 0, d.costo_real / qr.replace(0, np.nan), 0)
    cp = np.where(qp > 0, d.costo_ppto / qp.replace(0, np.nan), cr)
    d = d.assign(pr=pr, pp=pp, cr=cr, cp=cp, mp=pp - cp)

    efecto_precio = ((d.pr - d.pp) * qr).sum()
    efecto_costo = (-(d.cr - d.cp) * qr).sum()

    # margen unitario presupuestado promedio por categoría (ponderado por unidades ppto)
    cat = d.groupby("categoria_id").apply(
        lambda g: pd.Series({"Qr": g.unidades_real.sum(), "Qp": g.unidades_ppto.sum(),
                             "Mp": (g.mp * g.unidades_ppto).sum(),
                             "QrMp_celda": (g.unidades_real * g.mp).sum()}), include_groups=False)
    cat["mp_cat"] = cat.Mp / cat.Qp
    Qr, Qp = cat.Qr.sum(), cat.Qp.sum()
    mp_total = cat.Mp.sum() / Qp
    efecto_volumen = (Qr - Qp) * mp_total
    efecto_mix_cat = ((cat.Qr - Qr * cat.Qp / Qp) * cat.mp_cat).sum()
    efecto_mix_tienda = (cat.QrMp_celda - cat.Qr * cat.mp_cat).sum()

    total = (d.venta_real - d.costo_real).sum() - (d.venta_ppto - d.costo_ppto).sum()
    s = pd.Series({"Precio": efecto_precio, "Costo unitario": efecto_costo, "Volumen": efecto_volumen,
                   "Mix de categorías": efecto_mix_cat, "Mix de tiendas": efecto_mix_tienda})
    assert abs(s.sum() - total) < 1e-3 * max(1, abs(total)), (s.sum(), total)
    return s


def puente_ebitda(rvp, gastos, año, meses) -> pd.DataFrame:
    r = rvp[(rvp.año == año) & rvp.mes.isin(meses)]
    g = gastos[(gastos.año == año) & gastos.mes.isin(meses)]
    ebitda_ppto = (r.venta_ppto - r.costo_ppto).sum() - g.ppto.sum()
    pasos = [("EBITDA presupuesto", ebitda_ppto)]
    for k, v in puente_margen(r).items():
        pasos.append((k, v))
    for cuenta, x in g.groupby("cuenta"):
        pasos.append((f"Gasto: {cuenta}", -(x.real.sum() - x.ppto.sum())))
    ebitda_real = (r.venta_real - r.costo_real).sum() - g.real.sum()
    pasos.append(("EBITDA real", ebitda_real))
    df = pd.DataFrame(pasos, columns=["Concepto", "MM$"])
    df["MM$"] /= MM
    assert abs(df["MM$"].iloc[1:-1].sum() - (df["MM$"].iloc[-1] - df["MM$"].iloc[0])) < 1e-3
    return df


# ------------------------------------------------------------------
#  3. Proyección de cierre de mes
# ------------------------------------------------------------------
def proyectar(diaria: pd.DataFrame, año: int, mes: int, dia_corte: int) -> dict:
    """Proyección = venta a la fecha / participación que tuvo ese mismo tramo el año anterior."""
    act = diaria[(diaria.año == año) & (diaria.mes == mes)]
    ly = diaria[(diaria.año == año - 1) & (diaria.mes == mes)]
    mtd = act[act.dia <= dia_corte].venta.sum()
    share = ly[ly.dia <= dia_corte].venta.sum() / ly.venta.sum()
    dias_mes = (pd.Timestamp(año, mes, 1) + pd.offsets.MonthEnd(0)).day
    return {"mtd": mtd, "proyeccion": mtd / share, "lineal": mtd / dia_corte * dias_mes}


def error_proyeccion(diaria: pd.DataFrame, meses_cerrados: list, dia_corte: int) -> pd.DataFrame:
    filas = []
    for año, mes in meses_cerrados:
        if not ((diaria.año == año - 1) & (diaria.mes == mes)).any():
            continue
        real = diaria[(diaria.año == año) & (diaria.mes == mes)].venta.sum()
        p = proyectar(diaria, año, mes, dia_corte)
        filas.append({"Mes": f"{año}-{mes:02d}", "Real MM$": real / MM,
                      "Error método %": (p["proyeccion"] / real - 1) * 100,
                      "Error lineal %": (p["lineal"] / real - 1) * 100})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------
#  4. Alertas
# ------------------------------------------------------------------
def z_robusto(x: pd.Series) -> pd.Series:
    mad = (x - x.median()).abs().median() * 1.4826
    return (x - x.median()) / (mad if mad > 0 else np.nan)


def rachas(df: pd.DataFrame, claves: list, condicion: str, minimo: int) -> pd.DataFrame:
    """Marca filas donde la condición se cumple al menos `minimo` meses seguidos."""
    df = df.sort_values(claves + ["año", "mes"]).copy()
    df["_idx"] = df.año * 12 + df.mes
    out = []
    for k, g in df.groupby(claves):
        c = g[condicion].values
        run = 0
        for i, (_, fila) in enumerate(g.iterrows()):
            run = run + 1 if c[i] else 0
            if run >= minimo:
                out.append(fila)
    return pd.DataFrame(out)


def alertas(rvp, gastos, tiendas, cats, meses_cerrados) -> pd.DataFrame:
    cerr = pd.DataFrame(meses_cerrados, columns=["año", "mes"])
    lista = []

    # R1 Gasto atípico por tienda y cuenta.
    # Presupuesto flexible: los gastos variables se comparan como % de la venta (si una tienda vende
    # más de lo presupuestado, sus comisiones y mermas en pesos suben sin que haya un problema).
    g = gastos.merge(cerr).copy()
    variable = g.cuenta.isin(CUENTAS_VARIABLES)
    g["ratio"] = np.where(variable, (g.real / g.venta_real) / (g.ppto / g.venta_ppto), g.real / g.ppto)
    g["z"] = g.groupby(["año", "mes", "cuenta"]).ratio.transform(z_robusto)
    r1 = g[(g.ratio > 1 + R_GASTO_DESV) & (g.z > R_GASTO_Z)].merge(tiendas[["tienda_id", "tienda", "zona"]])
    for _, f in r1.iterrows():
        lista.append({"año": f.año, "mes": f.mes, "regla": "R1 Gasto atípico", "zona": f.zona,
                      "tienda": f.tienda, "tienda_id": f.tienda_id, "categoria": "", "cuenta": f.cuenta,
                      "detalle": f"{f.cuenta}: {f.ratio:.0%} del presupuesto (z={f.z:.1f})",
                      "impacto_MM$": (f.real - f.ppto) / MM})

    # Agregados zona x categoría
    zc = rvp.merge(cerr).merge(tiendas[["tienda_id", "zona"]]).groupby(["zona", "categoria_id", "año", "mes"]).agg(
        venta_real=("venta_real", "sum"), venta_ppto=("venta_ppto", "sum"),
        costo_real=("costo_real", "sum"), costo_ppto=("costo_ppto", "sum"),
        u_real=("unidades_real", "sum"), u_ppto=("unidades_ppto", "sum")).reset_index()
    zc["cumpl"] = zc.venta_real / zc.venta_ppto
    zc["bajo"] = zc.cumpl < R_VENTA_CUMPL
    r2 = rachas(zc, ["zona", "categoria_id"], "bajo", R_VENTA_MESES)
    for _, f in r2.iterrows():
        lista.append({"año": f.año, "mes": f.mes, "regla": "R2 Venta bajo presupuesto", "zona": f.zona,
                      "tienda": "", "tienda_id": "", "categoria": cats[f.categoria_id], "cuenta": "",
                      "detalle": f"cumplimiento {f.cumpl:.0%} ({R_VENTA_MESES}+ meses seguidos bajo {R_VENTA_CUMPL:.0%}); "
                                 f"unidades {f.u_real / f.u_ppto - 1:+.0%}",
                      "impacto_MM$": (f.venta_real - f.venta_ppto) / MM})

    # R3 Precio / margen por categoría (toda la cadena)
    c = rvp.merge(cerr).groupby(["categoria_id", "año", "mes"]).agg(
        venta_real=("venta_real", "sum"), venta_ppto=("venta_ppto", "sum"), costo_real=("costo_real", "sum"),
        costo_ppto=("costo_ppto", "sum"), u_real=("unidades_real", "sum"), u_ppto=("unidades_ppto", "sum")).reset_index()
    c["desv_precio"] = (c.venta_real / c.u_real) / (c.venta_ppto / c.u_ppto) - 1
    c["desv_margen"] = (1 - c.costo_real / c.venta_real) - (1 - c.costo_ppto / c.venta_ppto)
    c["malo"] = (c.desv_precio < R_PRECIO_DESV) | (c.desv_margen < R_MARGEN_PP)
    r3 = rachas(c, ["categoria_id"], "malo", R_PRECIO_MESES)
    for _, f in r3.iterrows():
        lista.append({"año": f.año, "mes": f.mes, "regla": "R3 Precio/margen bajo presupuesto", "zona": "Todas",
                      "tienda": "", "tienda_id": "", "categoria": cats[f.categoria_id], "cuenta": "",
                      "detalle": f"precio {f.desv_precio:+.1%}, margen {f.desv_margen * 100:+.1f} pp vs ppto",
                      "impacto_MM$": ((f.venta_real - f.costo_real) - (f.venta_ppto - f.costo_ppto)) / MM})
    al = pd.DataFrame(lista).sort_values(["año", "mes", "regla"]).reset_index(drop=True)

    # Recurrencia: la misma alerta (regla + tienda/zona + categoría + cuenta) en 2+ de los últimos 3 meses.
    # R2 y R3 ya exigen meses seguidos, así que nacen recurrentes.
    al["clave"] = al.regla + "|" + al.tienda_id + "|" + al.zona + "|" + al.categoria + "|" + al.cuenta
    al["t"] = al.año * 12 + al.mes
    n, v = RECURRENTE
    al["tipo"] = [
        "Recurrente" if not r.regla.startswith("R1")
        or ((al.clave == r.clave) & (al.t <= r.t) & (al.t > r.t - v)).sum() >= n else "Puntual"
        for r in al.itertuples()
    ]
    return al.drop(columns=["clave", "t"])


# ------------------------------------------------------------------
#  5. Verificación contra anomalías plantadas
# ------------------------------------------------------------------
PLANTADAS = [
    ("A1 Competidor en Electro Norte", (2026, 2), lambda a: (a.regla.str.startswith("R2")) & (a.zona == "Norte") & (a.categoria == "Electro")),
    ("A2 Liquidaciones Vestuario", (2026, 1), lambda a: (a.regla.str.startswith(("R2", "R3"))) & (a.categoria == "Vestuario")),
    ("A3 Merma anormal T07", (2025, 7), lambda a: (a.tienda_id == "T07") & (a.cuenta == "Mermas")),
    ("A4 Insumos irregulares T19", (2025, 10), lambda a: (a.tienda_id == "T19") & (a.cuenta == "Insumos y otros")),
    ("A5 Tarifas eléctricas Sur", (2026, 5), lambda a: (a.zona == "Sur") & (a.cuenta == "Servicios básicos")),
    # Evento REAL, no plantado: el dólar subió ~16% entre 2023 y 2024 y encareció los importados
    ("E1 Alza real del dólar (no plantada)", (2024, 1),
     lambda a: (a.regla.str.startswith("R3")) & (a.categoria.isin(["Electro", "Juguetería"]))),
]


def verificar(al: pd.DataFrame) -> tuple:
    filas, usadas = [], pd.Series(False, index=al.index)
    for nombre, (a0, m0), cond in PLANTADAS:
        m = cond(al) & ((al.año * 12 + al.mes) >= a0 * 12 + m0)
        usadas |= m
        fila = {"Anomalía": nombre}
        for tipo, sub in [("puntual", al[m]), ("recurrente", al[m & (al.tipo == "Recurrente")])]:
            if len(sub):
                p = sub.iloc[0]
                fila[f"1ª alerta {tipo}"] = f"{p.año}-{p.mes:02d}"
                fila[f"Demora {tipo} (meses)"] = (p.año * 12 + p.mes) - (a0 * 12 + m0)
            else:
                fila[f"1ª alerta {tipo}"], fila[f"Demora {tipo} (meses)"] = "no detectada", None
        filas.append(fila)
    return pd.DataFrame(filas), al[~usadas]


# ------------------------------------------------------------------
def main():
    OUT.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    ult, dias_mes, completo = periodo_actual(con)
    año, mes_act = ult.year, ult.month
    eerr = leer(con, "SELECT * FROM v_eerr_tienda_mes")
    rvp = leer(con, "SELECT * FROM v_real_vs_ppto")
    gastos = leer(con, "SELECT * FROM v_gastos_cuenta")
    diaria = leer(con, "SELECT * FROM v_venta_diaria")
    tiendas = leer(con, "SELECT * FROM dim_tienda")
    cats = leer(con, "SELECT * FROM dim_categoria").set_index("categoria_id").categoria

    todos = sorted({(a, m) for a, m in zip(eerr.año, eerr.mes) if (a, m) <= (año, mes_act)})
    cerrados = [x for x in todos if x != (año, mes_act) or completo]
    meses_ytd = [m for a, m in cerrados if a == año]

    # 1. KPIs
    k = pd.DataFrame({f"{año} ene-{meses_ytd[-1]:02d} (YTD)": kpis(eerr, año, meses_ytd),
                      f"{año - 1} mismo periodo": kpis(eerr, año - 1, meses_ytd),
                      f"{año - 1} año completo": kpis(eerr, año - 1, list(range(1, 13)))}).round(1)

    # 2. Puente
    puente = puente_ebitda(rvp, gastos, año, meses_ytd).round(1)

    # 3. Proyección del mes en curso + error histórico
    proy = proyectar(diaria, año, mes_act, ult.day)
    ppto_mes = eerr[(eerr.año == año) & (eerr.mes == mes_act)].venta_ppto.sum()
    meses_prueba = [x for x in cerrados if x[0] >= año - 1]
    err = error_proyeccion(diaria, meses_prueba, ult.day)
    err_cortes = pd.DataFrame([
        {"Corte (día del mes)": d,
         "Error método % (MAE)": (e := error_proyeccion(diaria, meses_prueba, d))["Error método %"].abs().mean(),
         "Error lineal % (MAE)": e["Error lineal %"].abs().mean(),
         "Peor error método %": e["Error método %"].abs().max(),
         "Peor error lineal %": e["Error lineal %"].abs().max()}
        for d in CORTES_PROYECCION + [ult.day]]).round(1)
    cierre = pd.DataFrame([{
        "Mes": f"{año}-{mes_act:02d}", "Días transcurridos": f"{ult.day} de {dias_mes}",
        "Venta a la fecha MM$": proy["mtd"] / MM, "Proyección cierre MM$": proy["proyeccion"] / MM,
        "Presupuesto MM$": ppto_mes / MM, "Cumplimiento proyectado %": proy["proyeccion"] / ppto_mes * 100,
        "Brecha MM$": (proy["proyeccion"] - ppto_mes) / MM,
        "Error típico del método % (MAE)": err["Error método %"].abs().mean(),
        "Error típico proyección lineal % (MAE)": err["Error lineal %"].abs().mean(),
    }]).round(1)

    # Ranking de tiendas YTD
    rk = (eerr[(eerr.año == año) & eerr.mes.isin(meses_ytd)]
          .groupby(["tienda_id", "tienda", "zona", "formato"])[["venta_real", "venta_ppto", "ebitda_real", "ebitda_ppto"]]
          .sum().reset_index())
    rk["Cumpl. venta %"] = rk.venta_real / rk.venta_ppto * 100
    rk["Desv. EBITDA MM$"] = (rk.ebitda_real - rk.ebitda_ppto) / MM
    rk["EBITDA % venta"] = rk.ebitda_real / rk.venta_real * 100
    rk = rk.sort_values("Desv. EBITDA MM$")
    neg = rk[rk["Desv. EBITDA MM$"] < 0]["Desv. EBITDA MM$"]
    concentr = neg.head(3).sum() / neg.sum() * 100 if len(neg) else 0

    # 4-5. Alertas y verificación
    al = alertas(rvp, gastos, tiendas, cats, cerrados)
    verif, falsos = verificar(al)

    # ---------- Consola ----------
    pd.set_option("display.width", 200)
    print(f"\nTIENDAS VALDORA (ficticia) · datos al {ult:%d-%m-%Y}\n" + "=" * 70)
    print(k.to_string())
    print("\nPuente EBITDA YTD (MM$):")
    print(puente.to_string(index=False))
    print(f"\nCierre proyectado {año}-{mes_act:02d}:")
    print(cierre.T.to_string(header=False))
    print(f"\nTop 3 tiendas explican {concentr:.0f}% de la desviación negativa de EBITDA:")
    print(rk.head(3)[["tienda", "zona", "Cumpl. venta %", "Desv. EBITDA MM$"]].round(1).to_string(index=False))
    print(f"\nError de la proyección de cierre en {len(meses_prueba)} meses pasados:")
    print(err_cortes.to_string(index=False))
    print(f"\nAlertas: {len(al)} en total")
    print(al.groupby(["regla", "tipo"]).size().unstack(fill_value=0).to_string())
    print("\nVerificación contra anomalías plantadas:")
    print(verif.to_string(index=False))
    fp = falsos.tipo.value_counts()
    print(f"\nFalsos positivos (alertas sin anomalía plantada detrás): "
          f"{fp.get('Puntual', 0)} puntuales · {fp.get('Recurrente', 0)} recurrentes")
    if len(falsos):
        print(falsos[["año", "mes", "regla", "tipo", "tienda", "zona", "categoria", "detalle"]].to_string(index=False))

    # ---------- Excel ----------
    ruta = OUT / "informe_control_gestion.xlsx"
    with pd.ExcelWriter(ruta, engine="openpyxl") as xw:
        k.to_excel(xw, sheet_name="KPIs")
        puente.to_excel(xw, sheet_name="Puente EBITDA", index=False)
        cierre.T.to_excel(xw, sheet_name="Cierre proyectado", header=False)
        err_cortes.to_excel(xw, sheet_name="Error proyección", index=False)
        err.round(2).to_excel(xw, sheet_name="Error proyección", index=False, startrow=len(err_cortes) + 3)
        rk.round(1).to_excel(xw, sheet_name="Ranking tiendas", index=False)
        al.round(2).to_excel(xw, sheet_name="Alertas", index=False)
        verif.to_excel(xw, sheet_name="Verificación", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 60)
    print(f"\nInforme: {ruta}")

    # Tablas para Power BI (fase 4)
    (BASE / "datos" / "powerbi").mkdir(exist_ok=True)
    al.assign(periodo=pd.to_datetime(dict(year=al.año, month=al.mes, day=1)),
              regla_corta=al.regla.str[:2].map({"R1": "Gastos", "R2": "Venta", "R3": "Precio/margen"})).to_csv(
        BASE / "datos" / "powerbi" / "alertas.csv", index=False, date_format="%Y-%m-%d")
    puente.assign(Concepto=puente.Concepto.str.replace("Gasto: ", "", regex=False), orden=range(len(puente))).to_csv(BASE / "datos" / "powerbi" / "puente_ebitda.csv", index=False)

    # Resumen para el dashboard (fase 3)
    (OUT / "resumen.json").write_text(json.dumps({
        "fecha_datos": ult.strftime("%Y-%m-%d"), "top3_concentracion_pct": round(concentr, 1),
        "verificacion": verif.to_dict(orient="records"),
        "falsos_positivos": {k: int(v) for k, v in falsos.tipo.value_counts().items()},
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
