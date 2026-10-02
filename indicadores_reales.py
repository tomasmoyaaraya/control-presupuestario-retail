"""Descarga indicadores económicos REALES de Chile desde mindicador.cl (datos del Banco Central).

Uso:  python indicadores_reales.py  ->  datos/reales/{uf,dolar,imacec}.csv

Se guardan en caché: generar_datos.py los lee de ahí, así la simulación es reproducible
aunque la API cambie o no esté disponible.
"""
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

DIR = Path(__file__).parent / "datos" / "reales"
AÑOS = range(2022, 2027)
SERIES = {"uf": "UF (pesos)", "dolar": "Dólar observado (pesos)", "imacec": "IMACEC (var. % 12 meses)"}


def bajar(serie: str, año: int) -> list:
    url = f"https://mindicador.cl/api/{serie}/{año}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r).get("serie", [])


def main():
    DIR.mkdir(parents=True, exist_ok=True)
    for serie, nombre in SERIES.items():
        filas = []
        for año in AÑOS:
            filas += bajar(serie, año)
            time.sleep(0.3)
        df = pd.DataFrame(filas)
        df["fecha"] = pd.to_datetime(df.fecha, utc=True).dt.tz_convert("America/Santiago").dt.date
        df = df.drop_duplicates("fecha").sort_values("fecha")
        df.to_csv(DIR / f"{serie}.csv", index=False)
        print(f"  {nombre:<26} {len(df):>5} datos · {df.fecha.min()} a {df.fecha.max()}")


if __name__ == "__main__":
    main()
