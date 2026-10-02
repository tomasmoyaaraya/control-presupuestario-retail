# Anomalías plantadas (clave de verificación)

Los datos son simulados. Estos problemas se escondieron **a propósito** en `generar_datos.py` para
comprobar después que el análisis y las alertas los detectan solos, sin mirar esta lista.

| # | Qué | Dónde | Desde | Efecto esperado en el análisis |
|---|---|---|---|---|
| A1 | Competidor nuevo | Electro, zona Norte (T01–T06) | feb-2026 | Desviación de **volumen** negativa (−28% de unidades) |
| A2 | Liquidaciones más agresivas | Vestuario, todas las tiendas | ene-2026 | Desviación de **precio** negativa (−9%), volumen +6%, margen −5 pp |
| A3 | Merma anormal (posible robo o falla de control) | T07 Valparaíso | jul-2025 | Mermas ~3,8% de la venta vs 1,2% presupuestado |
| A4 | Gastos irregulares en insumos | T19 Santiago Centro | oct-2025 | Meses con insumos 3–4 veces lo normal (~45% de los meses) |
| A5 | Alza de tarifas eléctricas | Servicios básicos, zona Sur | may-2026 | +30% en servicios básicos |
| — | Tiendas nuevas (no es anomalía) | T28 (mar-2025), T20 (sep-2025), T12 (abr-2026) | — | Rampa de ventas en los primeros meses |
