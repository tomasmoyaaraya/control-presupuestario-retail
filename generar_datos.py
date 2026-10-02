"""Fase 1 - Genera los datos de "Tiendas Valdora", una cadena de retail FICTICIA.

Uso:  python generar_datos.py

Salidas en datos/ (CSV):
  tiendas.csv, categorias.csv            maestros
  ventas_diarias.csv                     venta real por día x tienda x categoría (unidades, venta, costo)
  presupuesto_ventas.csv                 presupuesto mensual por tienda x categoría (unidades, precio, venta, costo)
  gastos_real.csv / gastos_presupuesto.csv  gastos operacionales mensuales por tienda y cuenta

Todo es simulado con semilla fija (reproducible). Los indicadores macro son REALES (UF, dólar e IMACEC del
Banco Central vía mindicador.cl, ver indicadores_reales.py); la empresa y sus ventas son ficticias. En anomalias_plantadas.md están los
problemas escondidos a propósito, para verificar después que el análisis los detecta.
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
DATOS = BASE / "datos"
rng = np.random.default_rng(2026)

INICIO_SIM = "2023-01-01"      # 2023 solo sirve de base para el presupuesto 2024
INICIO_EXPORT = "2024-01-01"
FIN = "2026-09-28"             # último día cerrado; septiembre 2026 es el "mes en curso"

# ------------------------------------------------------------------
#  Maestros
# ------------------------------------------------------------------
ZONAS = {"Norte": ["Arica", "Iquique", "Antofagasta", "Calama", "La Serena", "Copiapó"],
         "Centro": ["Valparaíso", "Viña del Mar", "Rancagua", "Talca", "Curicó", "San Antonio"],
         "RM": ["Providencia", "Las Condes", "Maipú", "La Florida", "Puente Alto", "Ñuñoa",
                "Santiago Centro", "Quilicura", "Peñalolén", "San Bernardo"],
         "Sur": ["Concepción", "Temuco", "Valdivia", "Osorno", "Puerto Montt", "Chillán", "Los Ángeles", "Punta Arenas"]}

FORMATOS = {"Grande": (1.6, 32), "Mediana": (1.0, 20), "Pequeña": (0.6, 12)}  # (factor de venta, dotación)

CATEGORIAS = pd.DataFrame([
    # id, nombre, precio base ene-2023 (CLP), unidades/día tienda mediana, margen bruto
    ("C1", "Hogar", 14_990, 55, 0.42),
    ("C2", "Electro", 119_990, 9, 0.22),
    ("C3", "Vestuario", 17_990, 70, 0.52),
    ("C4", "Deportes", 24_990, 26, 0.40),
    ("C5", "Juguetería", 13_990, 30, 0.45),
    ("C6", "Belleza", 8_990, 60, 0.48),
], columns=["categoria_id", "categoria", "precio_base", "unidades_base", "margen"])

# Estacionalidad mensual por categoría (ene..dic)
EST_MES = {
    "Hogar":      [0.90, 0.85, 0.95, 0.95, 1.00, 0.95, 0.95, 1.00, 1.00, 1.00, 1.10, 1.35],
    "Electro":    [0.85, 0.80, 0.85, 0.90, 0.95, 1.10, 1.05, 0.95, 1.05, 1.05, 1.25, 1.40],
    "Vestuario":  [0.95, 1.00, 1.10, 1.00, 1.05, 0.90, 0.85, 0.90, 1.05, 1.00, 1.05, 1.40],
    "Deportes":   [1.15, 1.05, 1.10, 0.95, 0.90, 0.85, 0.85, 0.90, 1.00, 1.05, 1.05, 1.30],
    "Juguetería": [0.70, 0.65, 0.75, 0.75, 0.85, 0.80, 0.95, 1.30, 0.85, 0.85, 1.00, 2.40],
    "Belleza":    [0.90, 0.85, 0.95, 0.95, 1.35, 0.95, 0.95, 0.95, 1.00, 1.00, 1.05, 1.40],
}
EST_DIA_SEMANA = [0.85, 0.85, 0.90, 0.95, 1.10, 1.35, 1.20]  # lun..dom

# Supuestos macro (aproximados)
ELASTICIDAD_IMACEC = 1.5   # el retail amplifica el ciclo económico
COSTO_IMPORTADO = {"Electro": 1.0, "Juguetería": 0.5}  # fracción del costo atada al dólar
META_VOLUMEN = 0.04      # el presupuesto pide +4% de unidades vs año anterior
IPC_PRESUPUESTADO = 0.04 # el presupuesto supone +4% de precio


def indicadores_reales(fechas: pd.DatetimeIndex) -> dict:
    """Series diarias reales (UF, dólar) y un índice de demanda mensual construido con el IMACEC."""
    dir_ = DATOS / "reales"
    if not (dir_ / "uf.csv").exists():
        raise SystemExit("Faltan los indicadores reales: corre primero  python indicadores_reales.py")
    rango = pd.date_range(fechas.min() - pd.Timedelta(days=400), fechas.max())
    serie = lambda n: (pd.read_csv(dir_ / f"{n}.csv", parse_dates=["fecha"]).set_index("fecha").valor
                       .reindex(rango).ffill().bfill())
    uf, dolar = serie("uf"), serie("dolar")

    # Índice de demanda: I(m) = I(m-12) x (1 + elasticidad x IMACEC 12 meses). Base 2022 = 1.
    imacec = pd.read_csv(dir_ / "imacec.csv", parse_dates=["fecha"]).set_index("fecha").valor
    meses = pd.period_range("2022-01", fechas.max(), freq="M")
    g = imacec.to_period("M").reindex(meses).ffill()          # meses sin dato: último conocido
    idx = pd.Series(1.0, index=meses)
    for m in meses[12:]:
        idx[m] = idx[m - 12] * (1 + ELASTICIDAD_IMACEC * g[m] / 100)
    demanda = idx / idx[pd.Period("2023-01", "M"):pd.Period("2023-12", "M")].mean()
    return {"uf": uf, "dolar": dolar, "demanda": demanda}


def eventos(fecha: pd.Timestamp) -> dict:
    """Multiplicadores (volumen, precio) por categoría en fechas comerciales chilenas."""
    m, d = fecha.month, fecha.day
    ev = {}
    if m == 6 and 2 <= d <= 4 or m == 10 and 6 <= d <= 8:          # CyberDay
        ev = {"Electro": (2.6, 0.82), "Hogar": (1.6, 0.88), "Vestuario": (1.5, 0.85), "Deportes": (1.5, 0.87)}
    elif m == 11 and 27 <= d <= 30:                                  # Black Friday
        ev = {"Electro": (2.4, 0.83), "Hogar": (1.5, 0.88), "Vestuario": (1.5, 0.85)}
    elif m == 9 and 15 <= d <= 19:                                   # Fiestas Patrias
        ev = {"Hogar": (1.3, 1.0), "Deportes": (0.8, 1.0), "Electro": (1.2, 1.0)}
    elif m == 5 and 5 <= d <= 11:                                    # Día de la Madre
        ev = {"Belleza": (1.8, 0.97), "Hogar": (1.2, 1.0)}
    elif m == 12 and 15 <= d <= 24:                                  # Navidad
        ev = {"Juguetería": (1.8, 1.0), "Electro": (1.3, 1.0)}
    elif m == 3 and d <= 10:                                         # vuelta a clases
        ev = {"Vestuario": (1.3, 1.0), "Deportes": (1.2, 1.0)}
    return ev


def crear_tiendas() -> pd.DataFrame:
    filas, n = [], 1
    formatos = list(FORMATOS)
    for zona, comunas in ZONAS.items():
        for comuna in comunas:
            if zona == "RM":
                fmt = rng.choice(formatos, p=[0.4, 0.4, 0.2])
            else:
                fmt = rng.choice(formatos, p=[0.2, 0.5, 0.3])
            filas.append({"tienda_id": f"T{n:02d}", "tienda": f"Valdora {comuna}", "comuna": comuna,
                          "zona": zona, "formato": fmt, "apertura": "2018-01-01"})
            n += 1
    t = pd.DataFrame(filas)
    # Tres aperturas recientes
    for tid, fecha in [("T28", "2025-03-01"), ("T20", "2025-09-01"), ("T12", "2026-04-01")]:
        t.loc[t.tienda_id == tid, "apertura"] = fecha
    t["factor_venta"] = [FORMATOS[f][0] * rng.lognormal(0, 0.12) for f in t.formato]
    t["dotacion"] = [FORMATOS[f][1] for f in t.formato]
    t["arriendo_uf"] = (t.factor_venta * 420 * rng.uniform(0.85, 1.25, len(t))).round(0)
    t.loc[t.zona == "RM", "arriendo_uf"] *= 1.25
    return t


# ------------------------------------------------------------------
#  Ventas reales
# ------------------------------------------------------------------
def simular_ventas(tiendas: pd.DataFrame) -> pd.DataFrame:
    fechas = pd.date_range(INICIO_SIM, FIN, freq="D")
    años = (fechas - pd.Timestamp(INICIO_SIM)).days / 365.25

    real = indicadores_reales(fechas)
    uf, dolar = real["uf"], real["dolar"]

    # Índice de precios: reajuste cada marzo según la variación REAL de la UF en 12 meses
    idx_precio = np.ones(len(fechas))
    nivel = 1.0
    for i, f in enumerate(fechas):
        if f.month == 3 and f.day == 1 and f.year > 2023:
            nivel *= uf[f] / uf[f - pd.DateOffset(years=1)]
        idx_precio[i] = nivel

    # Demanda: ciclo REAL del IMACEC (mensual)
    demanda = real["demanda"].reindex(fechas.to_period("M")).values

    # Dólar real (dólar / UF), con 60 días de desfase por inventario, relativo a 2023
    dolar_real = (dolar / uf).rolling(60, min_periods=1).mean().shift(60).bfill()
    dolar_rel = (dolar_real.reindex(fechas) / dolar_real["2023"].mean()).values

    dia_sem = np.array([EST_DIA_SEMANA[f.dayofweek] for f in fechas])
    ev_dias = [eventos(f) for f in fechas]
    bloques = []
    for _, t in tiendas.iterrows():
        apertura = pd.Timestamp(t.apertura)
        meses_abierta = (fechas - apertura).days / 30.4
        rampa = np.where(meses_abierta < 0, 0, np.clip(0.6 + 0.4 * meses_abierta / 8, 0.6, 1.0))
        tendencia_tienda = rng.normal(0.0, 0.015)          # crecimiento propio (además del ciclo)
        for _, c in CATEGORIAS.iterrows():
            est_mes = np.array([EST_MES[c.categoria][f.month - 1] for f in fechas])
            vol_ev = np.array([e.get(c.categoria, (1, 1))[0] for e in ev_dias])
            pre_ev = np.array([e.get(c.categoria, (1, 1))[1] for e in ev_dias])
            lam = (c.unidades_base * t.factor_venta * est_mes * dia_sem * vol_ev * rampa
                   * (1 + tendencia_tienda) ** años * demanda)
            precio = c.precio_base * idx_precio * pre_ev * rng.normal(1, 0.015, len(fechas))
            imp = COSTO_IMPORTADO.get(c.categoria, 0.0)
            factor_costo = 1 - imp + imp * dolar_rel
            margen = 1 - (1 - c.margen) * factor_costo + rng.normal(0, 0.01, len(fechas))

            # --- Anomalías plantadas (ver anomalias_plantadas.md) ---
            if t.zona == "Norte" and c.categoria == "Electro":       # A1: competidor nuevo
                lam = lam * np.where(fechas >= "2026-02-01", 0.72, 1.0)
            if c.categoria == "Vestuario":                            # A2: liquidaciones agresivas 2026
                en26 = fechas >= "2026-01-01"
                precio = precio * np.where(en26, 0.91, 1.0)
                lam = lam * np.where(en26, 1.06, 1.0)
                margen = margen - np.where(en26, 0.05, 0.0)

            unidades = rng.poisson(lam * rng.lognormal(0, 0.12, len(fechas)))
            venta = (unidades * precio).round(0)
            bloques.append(pd.DataFrame({
                "fecha": fechas, "tienda_id": t.tienda_id, "categoria_id": c.categoria_id,
                "unidades": unidades, "venta_neta": venta,
                "costo_venta": (venta * (1 - margen)).round(0),
            }))
    v = pd.concat(bloques, ignore_index=True)
    return v[v.unidades > 0]


# ------------------------------------------------------------------
#  Presupuesto de ventas (se arma cada año con el real del año anterior)
# ------------------------------------------------------------------
def armar_presupuesto(ventas: pd.DataFrame, tiendas: pd.DataFrame) -> pd.DataFrame:
    v = ventas.copy()
    v["año"], v["mes"] = v.fecha.dt.year, v.fecha.dt.month
    base = (v.groupby(["año", "mes", "tienda_id", "categoria_id"])
              .agg(unidades=("unidades", "sum"), venta=("venta_neta", "sum"), costo=("costo_venta", "sum"))
              .reset_index())
    base["precio"] = base.venta / base.unidades
    base["margen"] = 1 - base.costo / base.venta

    fmt = tiendas.set_index("tienda_id").formato
    ppto = []
    for año in (2024, 2025, 2026):
        prev = base[base.año == año - 1].copy()
        # Promedio por formato (para tiendas sin historia completa)
        prev["formato"] = prev.tienda_id.map(fmt)
        prom_fmt = prev.groupby(["formato", "mes", "categoria_id"])[["unidades", "precio", "margen"]].mean()
        for _, t in tiendas.iterrows():
            apertura = pd.Timestamp(t.apertura)
            if apertura.year > año:
                continue
            for mes in range(1, 13):
                inicio_mes = pd.Timestamp(año, mes, 1)
                if inicio_mes < apertura.replace(day=1):
                    continue
                for cid in CATEGORIAS.categoria_id:
                    fila = prev[(prev.tienda_id == t.tienda_id) & (prev.mes == mes) & (prev.categoria_id == cid)]
                    madura = apertura <= pd.Timestamp(año - 1, 1, 1) and len(fila)
                    if madura:
                        u, p, mg = fila.unidades.iloc[0], fila.precio.iloc[0], fila.margen.iloc[0]
                        u *= 1 + META_VOLUMEN
                    else:  # tienda nueva: promedio del formato con rampa conservadora
                        r = prom_fmt.loc[(t.formato, mes, cid)]
                        u, p, mg = r.unidades * 0.85, r.precio, r.margen
                    p *= 1 + IPC_PRESUPUESTADO
                    u = round(u)
                    ppto.append({"año": año, "mes": mes, "tienda_id": t.tienda_id, "categoria_id": cid,
                                 "unidades_ppto": u, "precio_ppto": round(p, 0),
                                 "venta_ppto": round(u * p, 0), "costo_ppto": round(u * p * (1 - mg), 0)})
    return pd.DataFrame(ppto)


# ------------------------------------------------------------------
#  Gastos operacionales mensuales (real y presupuesto)
# ------------------------------------------------------------------
def simular_gastos(ventas: pd.DataFrame, ppto: pd.DataFrame, tiendas: pd.DataFrame):
    v = ventas.copy()
    v["año"], v["mes"] = v.fecha.dt.year, v.fecha.dt.month
    venta_mes = v.groupby(["año", "mes", "tienda_id"]).venta_neta.sum()
    ppto_mes = ppto.groupby(["año", "mes", "tienda_id"]).venta_ppto.sum()

    meses = pd.period_range(INICIO_EXPORT, FIN, freq="M")
    uf_dia = indicadores_reales(pd.date_range(INICIO_SIM, FIN))["uf"]
    real, pres = [], []
    for per in meses:
        año, mes = per.year, per.month
        inicio = per.start_time
        dic_prev = pd.Timestamp(año - 1, 12, 1)
        # Real: UF del mes y sueldos reajustados cada enero con la variación REAL de la UF
        uf_r = uf_dia[inicio]
        sueldo_r = 880_000 * uf_dia[pd.Timestamp(año, 1, 1)] / uf_dia[pd.Timestamp(2023, 1, 1)]
        # Presupuesto: se armó en diciembre del año anterior suponiendo 4% anual
        uf_p = uf_dia[dic_prev] * (1 + IPC_PRESUPUESTADO) ** (mes / 12)
        sueldo_p = 880_000 * uf_dia[pd.Timestamp(año - 1, 1, 1)] / uf_dia[pd.Timestamp(2023, 1, 1)] * (1 + IPC_PRESUPUESTADO)
        for _, t in tiendas.iterrows():
            if pd.Timestamp(t.apertura) > per.end_time:
                continue
            vr = venta_mes.get((año, mes, t.tienda_id), 0)
            vp = ppto_mes.get((año, mes, t.tienda_id), 0)
            invierno = 1.25 if mes in (6, 7, 8) else 1.0
            base_serv = 2_600_000 * t.factor_venta * invierno

            # Presupuesto
            g_p = {
                "Arriendo": t.arriendo_uf * uf_p,
                "Remuneraciones": t.dotacion * sueldo_p * 1.08,
                "Comisiones": 0.010 * vp,
                "Servicios básicos": base_serv * uf_p / uf_dia[pd.Timestamp(2024, 1, 1)],
                "Mermas": 0.012 * vp,
                "Insumos y otros": 0.008 * vp,
            }
            # Real
            merma_pct = 0.012 * rng.lognormal(0, 0.15)
            insumos = 0.008 * vr * rng.lognormal(0, 0.12)
            if t.tienda_id == "T07" and per >= pd.Period("2025-07", "M"):   # A3: merma anormal
                merma_pct = 0.038 * rng.lognormal(0, 0.12)
            if t.tienda_id == "T19" and per >= pd.Period("2025-10", "M") and rng.random() < 0.45:  # A4
                insumos *= rng.uniform(2.8, 4.0)
            g_r = {
                "Arriendo": t.arriendo_uf * uf_r,
                "Remuneraciones": t.dotacion * sueldo_r * 1.08 * rng.normal(1, 0.03),
                "Comisiones": 0.010 * vr,
                "Servicios básicos": base_serv * uf_r / uf_dia[pd.Timestamp(2024, 1, 1)] * rng.lognormal(0, 0.08),
                "Mermas": merma_pct * vr,
                "Insumos y otros": insumos,
            }
            if t.zona == "Sur" and per >= pd.Period("2026-05", "M"):          # A5: alza de tarifas eléctricas
                g_r["Servicios básicos"] *= 1.30
            for cuenta in g_p:
                pres.append({"año": año, "mes": mes, "tienda_id": t.tienda_id, "cuenta": cuenta, "monto": round(g_p[cuenta])})
                real.append({"año": año, "mes": mes, "tienda_id": t.tienda_id, "cuenta": cuenta, "monto": round(g_r[cuenta])})
    return pd.DataFrame(real), pd.DataFrame(pres)


def main():
    DATOS.mkdir(exist_ok=True)
    tiendas = crear_tiendas()
    print("Simulando ventas diarias...")
    ventas = simular_ventas(tiendas)
    print("Armando presupuesto...")
    ppto = armar_presupuesto(ventas, tiendas)
    ventas = ventas[ventas.fecha >= INICIO_EXPORT]
    print("Simulando gastos...")
    g_real, g_ppto = simular_gastos(ventas, ppto, tiendas)

    tiendas.drop(columns=["factor_venta"]).to_csv(DATOS / "tiendas.csv", index=False)
    CATEGORIAS[["categoria_id", "categoria"]].to_csv(DATOS / "categorias.csv", index=False)
    ventas.to_csv(DATOS / "ventas_diarias.csv", index=False, date_format="%Y-%m-%d")
    ppto.to_csv(DATOS / "presupuesto_ventas.csv", index=False)
    g_real.to_csv(DATOS / "gastos_real.csv", index=False)
    g_ppto.to_csv(DATOS / "gastos_presupuesto.csv", index=False)
    for f in sorted(DATOS.glob("*.csv")):
        print(f"  {f.name:<26} {sum(1 for _ in open(f, encoding='utf-8')) - 1:>8,} filas")


if __name__ == "__main__":
    main()
