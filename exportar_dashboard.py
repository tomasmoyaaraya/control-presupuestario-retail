"""Fase 3 - Exporta los datos del tablero web a docs/data.js (lo lee docs/index.html).

Uso:  python exportar_dashboard.py
Reutiliza las funciones de analisis.py: el tablero muestra exactamente los mismos números que el informe Excel.
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

import analisis as A

BASE = Path(__file__).parent
DOCS = BASE / "docs"
MM = 1e6
ZONAS = ["Todas", "Norte", "Centro", "RM", "Sur"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def r1(x):
    return None if pd.isna(x) else round(float(x), 1)


def main():
    con = sqlite3.connect(A.DB)
    ult, dias_mes, completo = A.periodo_actual(con)
    año, mes_act = ult.year, ult.month
    eerr = A.leer(con, "SELECT * FROM v_eerr_tienda_mes")
    rvp = A.leer(con, "SELECT * FROM v_real_vs_ppto")
    gastos = A.leer(con, "SELECT * FROM v_gastos_cuenta")
    diaria = A.leer(con, "SELECT * FROM v_venta_diaria")
    tiendas = A.leer(con, "SELECT * FROM dim_tienda")
    cats = A.leer(con, "SELECT * FROM dim_categoria").set_index("categoria_id").categoria

    eerr = eerr[(eerr.año * 12 + eerr.mes) <= año * 12 + mes_act]
    todos = sorted({(a, m) for a, m in zip(eerr.año, eerr.mes)})
    cerrados = [x for x in todos if x != (año, mes_act) or completo]
    meses_ytd = [m for a, m in cerrados if a == año]
    rvp = rvp.merge(tiendas[["tienda_id", "zona"]])

    # Serie mensual por zona (el mes en curso va marcado como parcial)
    mensual = {}
    for z in ZONAS:
        d = eerr if z == "Todas" else eerr[eerr.zona == z]
        g = d.groupby(["año", "mes"])[["venta_real", "venta_ppto", "margen_real", "margen_ppto",
                                        "ebitda_real", "ebitda_ppto"]].sum().reset_index()
        mensual[z] = [{"mes": f"{f.año}-{f.mes:02d}", "parcial": (f.año, f.mes) == (año, mes_act) and not completo,
                       "venta_real": r1(f.venta_real / MM), "venta_ppto": r1(f.venta_ppto / MM),
                       "margen_real": r1(f.margen_real / MM), "margen_ppto": r1(f.margen_ppto / MM),
                       "ebitda_real": r1(f.ebitda_real / MM), "ebitda_ppto": r1(f.ebitda_ppto / MM)}
                      for f in g.itertuples()]

    # Año a la fecha por zona: KPIs y categorías
    ytd = {}
    for z in ZONAS:
        d = eerr if z == "Todas" else eerr[eerr.zona == z]
        k = A.kpis(d, año, meses_ytd)
        k_ly = A.kpis(d, año - 1, meses_ytd)
        rz = rvp if z == "Todas" else rvp[rvp.zona == z]
        rz = rz[(rz.año == año) & rz.mes.isin(meses_ytd)]
        c = rz.groupby("categoria_id")[["venta_real", "venta_ppto", "costo_real", "costo_ppto"]].sum()
        ytd[z] = {
            "kpis": {k2: r1(v) for k2, v in k.items()},
            "venta_ly": r1(k_ly["Venta real (MM$)"]), "ebitda_ly": r1(k_ly["EBITDA real (MM$)"]),
            "categorias": [{"categoria": cats[i], "venta_real": r1(f.venta_real / MM),
                            "cumplimiento": r1(f.venta_real / f.venta_ppto * 100),
                            "margen_real": r1((1 - f.costo_real / f.venta_real) * 100),
                            "margen_ppto": r1((1 - f.costo_ppto / f.venta_ppto) * 100)}
                           for i, f in c.iterrows()],
        }

    # Ranking de tiendas
    rk = (eerr[(eerr.año == año) & eerr.mes.isin(meses_ytd)]
          .groupby(["tienda_id", "tienda", "zona", "formato"])[["venta_real", "venta_ppto", "ebitda_real", "ebitda_ppto"]]
          .sum().reset_index())
    aperturas = tiendas.set_index("tienda_id").apertura
    tiendas_out = [{"id": f.tienda_id, "tienda": f.tienda.replace("Valdora ", ""), "zona": f.zona, "formato": f.formato,
                    "nueva": aperturas[f.tienda_id] >= f"{año - 1}-01-01",
                    "venta": r1(f.venta_real / MM), "cumplimiento": r1(f.venta_real / f.venta_ppto * 100),
                    "ebitda": r1(f.ebitda_real / MM), "desv_ebitda": r1((f.ebitda_real - f.ebitda_ppto) / MM),
                    "ebitda_pct": r1(f.ebitda_real / f.venta_real * 100)} for f in rk.itertuples()]

    # Puente, cierre y alertas (mismas funciones del informe)
    puente = A.puente_ebitda(rvp, gastos, año, meses_ytd)
    proy = A.proyectar(diaria, año, mes_act, ult.day)
    ppto_mes = eerr[(eerr.año == año) & (eerr.mes == mes_act)].venta_ppto.sum()
    pruebas = [x for x in cerrados if x[0] >= año - 1]
    err = []
    for d in A.CORTES_PROYECCION + [ult.day]:
        e = A.error_proyeccion(diaria, pruebas, d)
        err.append({"dia": d, "metodo": r1(e["Error método %"].abs().mean()), "lineal": r1(e["Error lineal %"].abs().mean())})
    al = A.alertas(rvp.drop(columns="zona"), gastos, tiendas, cats, cerrados)
    verif, falsos = A.verificar(al)

    # Estado de resultados año a la fecha por zona: año anterior (PY), presupuesto (PL) y real (AC)
    gz = gastos.merge(tiendas[["tienda_id", "zona"]])
    pnl = {}
    for z in ZONAS:
        r = rvp if z == "Todas" else rvp[rvp.zona == z]
        g = gz if z == "Todas" else gz[gz.zona == z]
        def bloque(a, col_v, col_c, col_g):
            rr = r[(r.año == a) & r.mes.isin(meses_ytd)]
            gg = g[(g.año == a) & g.mes.isin(meses_ytd)]
            filas = {"Venta": rr[col_v].sum(), "Costo de venta": -rr[col_c].sum()}
            filas["Margen bruto"] = filas["Venta"] + filas["Costo de venta"]
            for cuenta, x in gg.groupby("cuenta"):
                filas[cuenta] = -x[col_g].sum()
            filas["EBITDA"] = filas["Margen bruto"] + sum(v for k, v in filas.items()
                                                           if k not in ("Venta", "Costo de venta", "Margen bruto"))
            return filas
        ac, pl, py = bloque(año, "venta_real", "costo_real", "real"), bloque(año, "venta_ppto", "costo_ppto", "ppto"), \
            bloque(año - 1, "venta_real", "costo_real", "real")
        orden = ["Venta", "Costo de venta", "Margen bruto", "Remuneraciones", "Arriendo", "Comisiones",
                 "Servicios básicos", "Mermas", "Insumos y otros", "EBITDA"]
        pnl[z] = [{"linea": k, "total": k in ("Margen bruto", "EBITDA", "Venta"),
                   "py": r1(py.get(k, 0) / MM), "pl": r1(pl.get(k, 0) / MM), "ac": r1(ac.get(k, 0) / MM)} for k in orden]

    # Proyección del mes en curso por zona (mismo método)
    dz = A.leer(con, """SELECT f.fecha, f.año, f.mes, f.dia, t.zona, SUM(v.venta_neta) AS venta
                        FROM fact_ventas v JOIN dim_fecha f ON f.fecha = v.fecha
                        JOIN dim_tienda t ON t.tienda_id = v.tienda_id GROUP BY f.fecha, t.zona""")
    proy_zona = {"Todas": proy["proyeccion"] / MM}
    for z in ZONAS[1:]:
        proy_zona[z] = A.proyectar(dz[dz.zona == z], año, mes_act, ult.day)["proyeccion"] / MM
    for z in ZONAS:
        for m in mensual[z]:
            if m["parcial"]:
                m["proyeccion"] = r1(proy_zona[z])

    # Mensaje clave (estilo IBCS): qué pasó y por qué, en una frase
    k = ytd["Todas"]["kpis"]
    drivers = [p for p in puente.to_dict(orient="records")[1:-1]]
    peor = min(drivers, key=lambda p: p["MM$"])
    mejor = max(drivers, key=lambda p: p["MM$"])
    def frase(concepto, valor):
        """'Precio', -1503 -> 'los menores precios'. Para gastos, valor > 0 = gasto menor al presupuesto."""
        if concepto.startswith("Gasto: "):
            return f"el {'menor' if valor > 0 else 'mayor'} gasto en {concepto[7:].lower()}"
        base = {"Precio": ("los menores precios", "los mayores precios"),
                "Costo unitario": ("el mayor costo unitario", "el menor costo unitario"),
                "Volumen": ("el menor volumen", "el mayor volumen"),
                "Mix de categorías": ("el mix de categorías", "el mix de categorías"),
                "Mix de tiendas": ("el mix de tiendas", "el mix de tiendas")}[concepto]
        return base[valor > 0]

    fmt = lambda v: f"{abs(v):,.0f}".replace(",", ".")
    desv = k["EBITDA real (MM$)"] - k["EBITDA ppto (MM$)"]
    titular = (f"El EBITDA acumulado está {abs(100 - k['Cumplimiento EBITDA %']):.0f}% "
               f"{'bajo' if desv < 0 else 'sobre'} el presupuesto (${fmt(desv)} MM): "
               f"{frase(peor['Concepto'], peor['MM$'])} restaron ${fmt(peor['MM$'])} MM, "
               f"y {frase(mejor['Concepto'], mejor['MM$'])} compensó ${fmt(mejor['MM$'])} MM.")
    titular = titular.replace("el mayor costo unitario restaron", "el mayor costo unitario restó")

    data = {
        "empresa": "Tiendas Valdora", "fecha_datos": ult.strftime("%Y-%m-%d"), "año": año,
        "titular": titular, "pnl": pnl,
        "ytd_label": f"ene–{MESES[meses_ytd[-1] - 1]} {año}",
        "mensual": mensual, "ytd": ytd, "tiendas": tiendas_out,
        "puente": [{"concepto": f["Concepto"].replace("Gasto: ", ""), "valor": r1(f["MM$"]),
                    "tipo": "total" if i in (0, len(puente) - 1) else ("gasto" if f["Concepto"].startswith("Gasto") else "venta")}
                   for i, f in enumerate(puente.to_dict(orient="records"))],
        "cierre": {"mes": f"{año}-{mes_act:02d}", "dia": ult.day, "dias_mes": dias_mes,
                   "mtd": r1(proy["mtd"] / MM), "proyeccion": r1(proy["proyeccion"] / MM),
                   "lineal": r1(proy["lineal"] / MM), "ppto": r1(ppto_mes / MM), "error": err},
        "alertas": [{"periodo": f"{f['año']}-{f['mes']:02d}", "regla": f["regla"], "tipo": f["tipo"], "zona": f["zona"],
                     "tienda": f["tienda"].replace("Valdora ", ""), "categoria": f["categoria"], "cuenta": f["cuenta"],
                     "detalle": f["detalle"], "impacto": r1(f["impacto_MM$"])} for f in al.to_dict(orient="records")],
        "verificacion": [{k: (None if pd.isna(v) else v) for k, v in fila.items()} for fila in verif.to_dict(orient="records")],
        "falsos": {"puntual": int((falsos.tipo == "Puntual").sum()), "recurrente": int((falsos.tipo == "Recurrente").sum())},
    }
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.js").write_text("window.DATA = " + json.dumps(data, ensure_ascii=False, default=str) + ";\n",
                                  encoding="utf-8")
    print(f"docs/data.js listo · {len(data['alertas'])} alertas · {len(tiendas_out)} tiendas · datos al {data['fecha_datos']}")


if __name__ == "__main__":
    main()
