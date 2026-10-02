"""Fase 4 - Genera el proyecto de Power BI (formato .pbip: modelo en TMDL + informe en PBIR).

Uso:  python powerbi/generar_pbip.py   ->  powerbi/Valdora.pbip  (abrir con Power BI Desktop)

El modelo lee los CSV de datos/ (ruta en el parámetro CarpetaDatos). Las medidas DAX replican los KPIs
de analisis.py; las alertas y el puente de EBITDA vienen precalculados desde Python (datos/powerbi/).
"""
import json
import shutil
from pathlib import Path

PBI = Path(__file__).parent
DATOS = (PBI.parent / "datos").resolve()
NOMBRE = "Valdora"
SM = PBI / f"{NOMBRE}.SemanticModel"
RP = PBI / f"{NOMBRE}.Report"
T = "\t"


# ======================================================================
#  Modelo semántico (TMDL)
# ======================================================================
def m_csv(archivo: str, tipos: list, extra: str = "") -> str:
    """Consulta M que lee un CSV (UTF-8, punto decimal) y tipa las columnas con cultura en-US."""
    t = ", ".join(f'{{"{c}", {ty}}}' for c, ty in tipos)
    pasos = [
        f'Origen = Csv.Document(File.Contents(CarpetaDatos & "\\{archivo}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])',
        "Encabezados = Table.PromoteHeaders(Origen, [PromoteAllScalars=true])",
        f'Tipos = Table.TransformColumnTypes(Encabezados, {{{t}}}, "en-US")',
    ]
    ultimo = "Tipos"
    if extra:
        pasos.append(extra)
        ultimo = extra.split("=")[0].strip()
    return "let\n" + ",\n".join("    " + p for p in pasos) + f"\nin\n    {ultimo}"


TIPOS_DAX = {"string": "type text", "int64": "Int64.Type", "double": "type number", "dateTime": "type date"}


def columna(nombre, tipo, fmt=None, oculta=False, resumir="none", sort_by=None):
    q = f"'{nombre}'" if not nombre.isidentifier() or " " in nombre else nombre
    s = f"{T}column {q}\n{T*2}dataType: {tipo}\n"
    if fmt:
        s += f"{T*2}formatString: {fmt}\n"
    if oculta:
        s += f"{T*2}isHidden\n"
    if sort_by:
        s += f"{T*2}sortByColumn: {sort_by}\n"
    s += f"{T*2}summarizeBy: {resumir}\n{T*2}sourceColumn: {nombre}\n\n"
    return s


def tabla(nombre, columnas, m, extra_props="", medidas=""):
    s = f"table {nombre}\n{extra_props}\n"
    s += medidas
    for c in columnas:
        s += columna(*c) if isinstance(c, tuple) else columna(**c)
    src = "\n".join(T * 4 + linea for linea in m.splitlines())
    s += f"{T}partition {nombre} = m\n{T*2}mode: import\n{T*2}source =\n{src}\n\n"
    return s


def medida(nombre, expr, fmt, carpeta, desc=""):
    expr_lines = expr.strip().splitlines()
    if len(expr_lines) == 1:
        s = f"{T}measure '{nombre}' = {expr_lines[0]}\n"
    else:
        s = f"{T}measure '{nombre}' =\n" + "\n".join(T * 3 + l for l in expr_lines) + "\n"
    if fmt:
        s += f"{T*2}formatString: {fmt}\n"
    s += f"{T*2}displayFolder: {carpeta}\n"
    if desc:
        s += f"{T*2}/// {desc}\n" if False else ""
    return s + "\n"


MM = r"\$#,0"          # pesos (las tablas usan las medidas en MM$)
PCT = "0.0%"
FMT_MM = r'\$#,0" MM"'   # millones de pesos
LINEAS_PNL = ["Venta", "Costo de venta", "Margen bruto", "Remuneraciones", "Arriendo", "Comisiones",
              "Servicios básicos", "Mermas", "Insumos y otros", "EBITDA"]
PNL = ("VAR f = [Último Mes Cerrado]\n"
       "VAR l = SELECTEDVALUE(LineasPnL[Linea])\n"
       "VAR r = CALCULATE(\n"
       "    SWITCH(l, \"Venta\", {v}, \"Costo de venta\", -{c}, \"Margen bruto\", {m}, \"EBITDA\", {e},\n"
       "        -CALCULATE({g}, Gastos[cuenta] = l)),\n"
       "    REMOVEFILTERS(Calendario), Calendario[Año] = {y}, Calendario[Fecha] <= {hasta})\n"
       "RETURN DIVIDE(r, 1e6)")
MEDIDAS = [
    # --- base
    ("Venta Real", "SUM(Ventas[venta_neta])", MM, "1 Base"),
    ("Costo Real", "SUM(Ventas[costo_venta])", MM, "1 Base"),
    ("Unidades Real", "SUM(Ventas[unidades])", "#,0", "1 Base"),
    ("Venta Ppto", "SUM(Presupuesto[venta_ppto])", MM, "1 Base"),
    ("Costo Ppto", "SUM(Presupuesto[costo_ppto])", MM, "1 Base"),
    ("Unidades Ppto", "SUM(Presupuesto[unidades_ppto])", "#,0", "1 Base"),
    ("Gastos Real", 'CALCULATE(SUM(Gastos[monto]), Gastos[escenario] = "real")', MM, "1 Base"),
    ("Gastos Ppto", 'CALCULATE(SUM(Gastos[monto]), Gastos[escenario] = "ppto")', MM, "1 Base"),
    ("Margen Real", "[Venta Real] - [Costo Real]", MM, "1 Base"),
    ("Margen Ppto", "[Venta Ppto] - [Costo Ppto]", MM, "1 Base"),
    ("EBITDA Real", "[Margen Real] - [Gastos Real]", MM, "1 Base"),
    ("EBITDA Ppto", "[Margen Ppto] - [Gastos Ppto]", MM, "1 Base"),
    # --- ratios
    ("Cumplimiento Venta %", "DIVIDE([Venta Real], [Venta Ppto])", PCT, "2 Ratios"),
    ("Margen Real %", "DIVIDE([Margen Real], [Venta Real])", PCT, "2 Ratios"),
    ("Margen Ppto %", "DIVIDE([Margen Ppto], [Venta Ppto])", PCT, "2 Ratios"),
    ("Cumplimiento EBITDA %", "DIVIDE([EBITDA Real], [EBITDA Ppto])", PCT, "2 Ratios"),
    ("EBITDA % Venta", "DIVIDE([EBITDA Real], [Venta Real])", PCT, "2 Ratios"),
    ("Venta Real Año Anterior", "CALCULATE([Venta Real], SAMEPERIODLASTYEAR(Calendario[Fecha]))", MM, "2 Ratios"),
    ("Crecimiento vs Año Anterior %", "DIVIDE([Venta Real], [Venta Real Año Anterior]) - 1", PCT, "2 Ratios"),
    # --- año a la fecha (meses cerrados del último año con datos)
    ("Último Mes Cerrado",
     "VAR ult = CALCULATE(MAX(Ventas[fecha]), REMOVEFILTERS())\nRETURN IF(ult = EOMONTH(ult, 0), ult, EOMONTH(ult, -1))",
     "dd-mm-yyyy", "3 Año a la fecha"),
    ("Venta Real YTD", "VAR f = [Último Mes Cerrado]\nRETURN CALCULATE([Venta Real], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f), Calendario[Fecha] <= f)", MM, "3 Año a la fecha"),
    ("Venta Ppto YTD", "VAR f = [Último Mes Cerrado]\nRETURN CALCULATE([Venta Ppto], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f), Calendario[Fecha] <= f)", MM, "3 Año a la fecha"),
    ("Margen Real YTD", "VAR f = [Último Mes Cerrado]\nRETURN CALCULATE([Margen Real], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f), Calendario[Fecha] <= f)", MM, "3 Año a la fecha"),
    ("EBITDA Real YTD", "VAR f = [Último Mes Cerrado]\nRETURN CALCULATE([EBITDA Real], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f), Calendario[Fecha] <= f)", MM, "3 Año a la fecha"),
    ("EBITDA Ppto YTD", "VAR f = [Último Mes Cerrado]\nRETURN CALCULATE([EBITDA Ppto], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f), Calendario[Fecha] <= f)", MM, "3 Año a la fecha"),
    ("Cumplimiento Venta YTD %", "DIVIDE([Venta Real YTD], [Venta Ppto YTD])", PCT, "3 Año a la fecha"),
    ("Margen Real YTD %", "DIVIDE([Margen Real YTD], [Venta Real YTD])", PCT, "3 Año a la fecha"),
    ("Cumplimiento EBITDA YTD %", "DIVIDE([EBITDA Real YTD], [EBITDA Ppto YTD])", PCT, "3 Año a la fecha"),
    ("Desviación EBITDA YTD", "[EBITDA Real YTD] - [EBITDA Ppto YTD]", MM, "3 Año a la fecha"),
    ("Venta YTD (MM$)", "DIVIDE([Venta Real YTD], 1e6)", FMT_MM, "3 Año a la fecha"),
    ("EBITDA YTD (MM$)", "DIVIDE([EBITDA Real YTD], 1e6)", FMT_MM, "3 Año a la fecha"),
    ("Desv. EBITDA YTD (MM$)", "DIVIDE([Desviación EBITDA YTD], 1e6)", FMT_MM, "3 Año a la fecha"),
    # --- efecto precio (nivel tienda x categoría x mes, igual que analisis.py)
    ("Efecto Precio",
     "SUMX(\n    ADDCOLUMNS(\n        SUMMARIZE(Presupuesto, Tiendas[tienda_id], Categorias[categoria_id], Calendario[AñoMes]),\n"
     "        \"@qr\", [Unidades Real], \"@vr\", [Venta Real], \"@qp\", [Unidades Ppto], \"@vp\", [Venta Ppto]\n    ),\n"
     "    IF([@qr] > 0 && [@qp] > 0, (DIVIDE([@vr], [@qr]) - DIVIDE([@vp], [@qp])) * [@qr])\n)",
     MM, "4 Desviaciones"),
    ("Efecto Volumen y Mix", "([Venta Real] - [Venta Ppto]) - [Efecto Precio]", MM, "4 Desviaciones"),
    ("Efecto Precio YTD (MM$)", "VAR f = [Último Mes Cerrado]\nRETURN DIVIDE(CALCULATE([Efecto Precio], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f), Calendario[Fecha] <= f), 1e6)", FMT_MM, "4 Desviaciones"),
    ("Efecto Volumen y Mix YTD (MM$)", "VAR f = [Último Mes Cerrado]\nRETURN DIVIDE(CALCULATE([Efecto Volumen y Mix], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f), Calendario[Fecha] <= f), 1e6)", FMT_MM, "4 Desviaciones"),
    # --- desviación mensual (solo meses con venta real), separada en favorable / desfavorable para colorear
    # Solo meses CERRADOS: el mes en curso (parcial) compararía 28 días contra un presupuesto de 30 y parecería una caída
    ("Δ Venta vs Ppto (MM$)", "IF(MAX(Calendario[Fecha]) <= [Último Mes Cerrado] && [Venta Real] > 0, DIVIDE([Venta Real] - [Venta Ppto], 1e6))", FMT_MM, "4 Desviaciones"),
    ("Δ Favorable (MM$)", "VAR d = [Δ Venta vs Ppto (MM$)] RETURN IF(d >= 0, d)", FMT_MM, "4 Desviaciones"),
    ("Δ Desfavorable (MM$)", "VAR d = [Δ Venta vs Ppto (MM$)] RETURN IF(d < 0, d)", FMT_MM, "4 Desviaciones"),
    ("Venta Real (MM$)", "IF(MAX(Calendario[Fecha]) <= [Último Mes Cerrado] && [Venta Real] > 0, DIVIDE([Venta Real], 1e6))", FMT_MM, "4 Desviaciones"),
    ("Venta Ppto (MM$)", "IF(MAX(Calendario[Fecha]) <= [Último Mes Cerrado] && [Venta Real] > 0, DIVIDE([Venta Ppto], 1e6))", FMT_MM, "4 Desviaciones"),
    # --- ventas vs año anterior y por zona/tienda (página Ventas)
    ("Venta Año Ant. (MM$)", "IF(MAX(Calendario[Fecha]) <= [Último Mes Cerrado] && [Venta Real] > 0 && NOT ISBLANK([Venta Real Año Anterior]), DIVIDE([Venta Real Año Anterior], 1e6))", FMT_MM, "4 Desviaciones"),
    ("Crecimiento mensual vs AA %", "IF(NOT ISBLANK([Venta Año Ant. (MM$)]), DIVIDE([Venta Real (MM$)], [Venta Año Ant. (MM$)]) - 1)", PCT, "4 Desviaciones"),
    ("Crecimiento YTD vs AA %", "VAR f = [Último Mes Cerrado]\nRETURN DIVIDE([Venta Real YTD], CALCULATE([Venta Real], REMOVEFILTERS(Calendario), Calendario[Año] = YEAR(f) - 1, Calendario[Fecha] <= EDATE(f, -12))) - 1", PCT, "3 Año a la fecha"),
    ("Δ Venta YTD vs Ppto (MM$)", "DIVIDE([Venta Real YTD] - [Venta Ppto YTD], 1e6)", FMT_MM, "3 Año a la fecha"),
    ("Δ Favorable YTD (MM$)", "VAR d = [Δ Venta YTD vs Ppto (MM$)] RETURN IF(d >= 0, d)", FMT_MM, "3 Año a la fecha"),
    ("Δ Desfavorable YTD (MM$)", "VAR d = [Δ Venta YTD vs Ppto (MM$)] RETURN IF(d < 0, d)", FMT_MM, "3 Año a la fecha"),
    # --- cascada: presupuesto + pasos (el total real lo calcula el visual)
    ("Paso Puente (MM$)", 'IF(SELECTEDVALUE(PuenteEBITDA[Concepto]) = "EBITDA real", BLANK(), SUM(PuenteEBITDA[MM$]))', FMT_MM, "4 Desviaciones"),
    # --- estado de resultados año a la fecha (una medida por escenario; la línea viene de LineasPnL)
    ("P&L Real", PNL.format(esc="Real", v="[Venta Real]", c="[Costo Real]", m="[Margen Real]", e="[EBITDA Real]", g="[Gastos Real]", y="YEAR(f)", hasta="f"), "#,0", "6 Estado de resultados"),
    ("P&L Ppto", PNL.format(esc="Ppto", v="[Venta Ppto]", c="[Costo Ppto]", m="[Margen Ppto]", e="[EBITDA Ppto]", g="[Gastos Ppto]", y="YEAR(f)", hasta="f"), "#,0", "6 Estado de resultados"),
    ("P&L Año Anterior", PNL.format(esc="AA", v="[Venta Real]", c="[Costo Real]", m="[Margen Real]", e="[EBITDA Real]", g="[Gastos Real]", y="YEAR(f) - 1", hasta="EDATE(f, -12)"), "#,0", "6 Estado de resultados"),
    ("Δ Ppto", "[P&L Real] - [P&L Ppto]", "+#,0;-#,0;0", "6 Estado de resultados"),
    ("Δ Ppto %", "DIVIDE([Δ Ppto], ABS([P&L Ppto]))", "+0.0%;-0.0%;0.0%", "6 Estado de resultados"),
    ("Δ AA %", "DIVIDE([P&L Real] - [P&L Año Anterior], ABS([P&L Año Anterior]))", "+0.0%;-0.0%;0.0%", "6 Estado de resultados"),
    ("Color Δ AA", 'IF([Δ AA %] >= 0, "#3B9A52", "#D6453D")', "", "6 Estado de resultados"),
    ("Color Desv Tienda", 'IF([Desviación EBITDA YTD] >= 0, "#3B9A52", "#D6453D")', "", "6 Estado de resultados"),
    ("Color Δ", 'IF([Δ Ppto] >= 0, "#3B9A52", "#D6453D")', "", "6 Estado de resultados"),
    # --- alertas
    ("Alertas", "COUNTROWS(Alertas)", "#,0", "5 Alertas"),
    ("Alertas Recurrentes", 'CALCULATE(COUNTROWS(Alertas), Alertas[tipo] = "Recurrente")', "#,0", "5 Alertas"),
]


def generar_modelo():
    defn = SM / "definition"
    (defn / "tables").mkdir(parents=True, exist_ok=True)
    (SM / "definition.pbism").write_text(json.dumps({"version": "4.0", "settings": {}}, indent=2), encoding="utf-8")
    (defn / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1606\n", encoding="utf-8")

    tablas = ["Ventas", "Presupuesto", "Gastos", "Tiendas", "Categorias", "Calendario", "Alertas", "PuenteEBITDA", "LineasPnL", "Medidas"]
    model = ("model Model\n\tculture: es-CL\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
             "\tsourceQueryCulture: es-CL\n\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n\n"
             + "".join(f"ref table {t}\n" for t in tablas) + "\nref expression CarpetaDatos\n")
    (defn / "model.tmdl").write_text(model, encoding="utf-8")

    ruta = str(DATOS).replace("\\", "\\\\")
    (defn / "expressions.tmdl").write_text(
        f'expression CarpetaDatos = "{DATOS}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n',
        encoding="utf-8")

    fecha_mes = 'ConFecha = Table.AddColumn(Tipos, "Fecha", each #date([año], [mes], 1), type date)'
    tb = {
        "Ventas": tabla("Ventas", [
            ("fecha", "dateTime", "dd-mm-yyyy"), ("tienda_id", "string", None, True), ("categoria_id", "string", None, True),
            ("unidades", "int64", "#,0", False, "sum"), ("venta_neta", "double", "#,0", False, "sum"),
            ("costo_venta", "double", "#,0", False, "sum")],
            m_csv("ventas_diarias.csv", [("fecha", "type date"), ("tienda_id", "type text"), ("categoria_id", "type text"),
                                         ("unidades", "Int64.Type"), ("venta_neta", "type number"), ("costo_venta", "type number")])),
        "Presupuesto": tabla("Presupuesto", [
            ("año", "int64", "0", True), ("mes", "int64", "0", True), ("tienda_id", "string", None, True),
            ("categoria_id", "string", None, True), ("unidades_ppto", "int64", "#,0", False, "sum"),
            ("precio_ppto", "double", "#,0", False, "none"), ("venta_ppto", "double", "#,0", False, "sum"),
            ("costo_ppto", "double", "#,0", False, "sum"), ("Fecha", "dateTime", "dd-mm-yyyy")],
            m_csv("presupuesto_ventas.csv", [("año", "Int64.Type"), ("mes", "Int64.Type"), ("tienda_id", "type text"),
                                             ("categoria_id", "type text"), ("unidades_ppto", "Int64.Type"),
                                             ("precio_ppto", "type number"), ("venta_ppto", "type number"),
                                             ("costo_ppto", "type number")], fecha_mes)),
        "Gastos": tabla("Gastos", [
            ("año", "int64", "0", True), ("mes", "int64", "0", True), ("tienda_id", "string", None, True),
            ("cuenta", "string"), ("monto", "double", "#,0", False, "sum"), ("escenario", "string"),
            ("Fecha", "dateTime", "dd-mm-yyyy")],
            "let\n"
            '    Leer = (archivo as text, escenario as text) =>\n'
            '        let\n'
            '            Origen = Csv.Document(File.Contents(CarpetaDatos & "\\" & archivo), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
            '            Encabezados = Table.PromoteHeaders(Origen, [PromoteAllScalars=true]),\n'
            '            Tipos = Table.TransformColumnTypes(Encabezados, {{"año", Int64.Type}, {"mes", Int64.Type}, {"tienda_id", type text}, {"cuenta", type text}, {"monto", type number}}, "en-US")\n'
            '        in\n'
            '            Table.AddColumn(Tipos, "escenario", each escenario, type text),\n'
            '    Union = Table.Combine({Leer("gastos_real.csv", "real"), Leer("gastos_presupuesto.csv", "ppto")}),\n'
            '    ConFecha = Table.AddColumn(Union, "Fecha", each #date([año], [mes], 1), type date)\n'
            "in\n    ConFecha"),
        "Tiendas": tabla("Tiendas", [
            ("tienda_id", "string"), ("tienda", "string"), ("comuna", "string"), ("zona", "string"),
            ("formato", "string"), ("apertura", "dateTime", "dd-mm-yyyy"), ("dotacion", "int64", "0"),
            ("arriendo_uf", "double", "#,0")],
            m_csv("tiendas.csv", [("tienda_id", "type text"), ("tienda", "type text"), ("comuna", "type text"),
                                  ("zona", "type text"), ("formato", "type text"), ("apertura", "type date"),
                                  ("dotacion", "Int64.Type"), ("arriendo_uf", "type number")])),
        "Categorias": tabla("Categorias", [("categoria_id", "string"), ("categoria", "string")],
                            m_csv("categorias.csv", [("categoria_id", "type text"), ("categoria", "type text")])),
        "Calendario": tabla("Calendario", [
            ("Fecha", "dateTime", "dd-mm-yyyy"), ("Año", "int64", "0"), ("Mes", "int64", "0"),
            {"nombre": "MesNombre", "tipo": "string", "sort_by": "Mes"},
            ("AñoMes", "string"), ("PeriodoOrden", "int64", "0", True),
            {"nombre": "Periodo", "tipo": "string", "sort_by": "PeriodoOrden"}],
            "let\n"
            "    Inicio = #date(2024, 1, 1),\n    Fin = #date(2026, 12, 31),\n"
            "    Fechas = List.Dates(Inicio, Duration.Days(Fin - Inicio) + 1, #duration(1, 0, 0, 0)),\n"
            '    Tabla = Table.FromList(Fechas, Splitter.SplitByNothing(), {"Fecha"}),\n'
            '    Tipada = Table.TransformColumnTypes(Tabla, {{"Fecha", type date}}),\n'
            '    ConAño = Table.AddColumn(Tipada, "Año", each Date.Year([Fecha]), Int64.Type),\n'
            '    ConMes = Table.AddColumn(ConAño, "Mes", each Date.Month([Fecha]), Int64.Type),\n'
            '    ConNombre = Table.AddColumn(ConMes, "MesNombre", each Text.Start(Date.MonthName([Fecha], "es-CL"), 3), type text),\n'
            '    ConAñoMes = Table.AddColumn(ConNombre, "AñoMes", each Date.ToText([Fecha], "yyyy-MM"), type text),\n'
            '    ConOrden = Table.AddColumn(ConAñoMes, "PeriodoOrden", each Date.Year([Fecha]) * 100 + Date.Month([Fecha]), Int64.Type),\n'
            '    ConPeriodo = Table.AddColumn(ConOrden, "Periodo", each [MesNombre] & " " & Text.End(Text.From(Date.Year([Fecha])), 2), type text)\n'
            "in\n    ConPeriodo",
            extra_props="\tdataCategory: Time\n"),
        "Alertas": tabla("Alertas", [
            ("periodo", "dateTime", "mmm yyyy"), ("regla", "string"), ("regla_corta", "string"), ("tipo", "string"),
            ("zona", "string"), ("tienda", "string"), ("categoria", "string"), ("cuenta", "string"), ("detalle", "string"),
            ("impacto_MM$", "double", "#,0.0", False, "sum")],
            m_csv("powerbi\\alertas.csv", [("periodo", "type date"), ("regla", "type text"), ("regla_corta", "type text"),
                                           ("tipo", "type text"), ("zona", "type text"), ("tienda", "type text"),
                                           ("categoria", "type text"), ("cuenta", "type text"), ("detalle", "type text"),
                                           ("impacto_MM$", "type number")])),
        "PuenteEBITDA": tabla("PuenteEBITDA", [
            {"nombre": "Concepto", "tipo": "string", "sort_by": "orden"}, ("MM$", "double", "#,0.0", False, "sum"),
            ("orden", "int64", "0", True)],
            m_csv("powerbi\\puente_ebitda.csv", [("Concepto", "type text"), ("MM$", "type number"), ("orden", "Int64.Type")])),
        "LineasPnL": tabla("LineasPnL", [{"nombre": "Linea", "tipo": "string", "sort_by": "Orden"},
                                          ("Orden", "int64", "0", True)],
                           "let\n    Lineas = #table(type table [Linea = text, Orden = Int64.Type], {\n"
                           + ",\n".join(f'        {{"{l}", {i}}}' for i, l in enumerate(LINEAS_PNL))
                           + "\n    })\nin\n    Lineas"),
        "Medidas": tabla("Medidas", [("x", "string", None, True)],
                         'let\n    Vacia = #table(type table [x = text], {})\nin\n    Vacia',
                         medidas="".join(medida(*m) for m in MEDIDAS)),
    }
    for nombre, txt in tb.items():
        (defn / "tables" / f"{nombre}.tmdl").write_text(txt, encoding="utf-8")

    rels = [("Ventas.fecha", "Calendario.Fecha"), ("Presupuesto.Fecha", "Calendario.Fecha"),
            ("Gastos.Fecha", "Calendario.Fecha"), ("Ventas.tienda_id", "Tiendas.tienda_id"),
            ("Presupuesto.tienda_id", "Tiendas.tienda_id"), ("Gastos.tienda_id", "Tiendas.tienda_id"),
            ("Ventas.categoria_id", "Categorias.categoria_id"), ("Presupuesto.categoria_id", "Categorias.categoria_id")]
    q = lambda ref: ".".join(f"'{p}'" if not p.isascii() else p for p in ref.split("."))
    (defn / "relationships.tmdl").write_text(
        "".join(f"relationship r{i}\n\tfromColumn: {q(a)}\n\ttoColumn: {q(b)}\n\n" for i, (a, b) in enumerate(rels, 1)),
        encoding="utf-8")


# ======================================================================
#  Informe (PBIR)
# ======================================================================
SCH = "https://developer.microsoft.com/json-schemas/fabric/item/report"


def campo(entidad, prop, medida=False, nombre=None):
    k = "Measure" if medida else "Column"
    d = {"field": {k: {"Expression": {"SourceRef": {"Entity": entidad}}, "Property": prop}},
         "queryRef": f"{entidad}.{prop}", "nativeQueryRef": prop}
    if nombre:  # encabezado corto en el visual (no cambia el nombre en el modelo)
        d["displayName"] = nombre
    return d


def suma(entidad, prop):
    return {"field": {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Entity": entidad}},
                                                                 "Property": prop}}, "Function": 0}},
            "queryRef": f"Sum({entidad}.{prop})", "nativeQueryRef": f"Suma de {prop}"}


def titulo(texto):
    return {"title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}},
                                      "text": {"expr": {"Literal": {"Value": f"'{texto}'"}}}}}]}


def visual(nombre, tipo, pos, roles: dict, title=None, z=0):
    v = {"$schema": f"{SCH}/definition/visualContainer/1.0.0/schema.json", "name": nombre,
         "position": {"x": pos[0], "y": pos[1], "z": z, "width": pos[2], "height": pos[3]},
         "visual": {"visualType": tipo,
                    "query": {"queryState": {r: {"projections": p} for r, p in roles.items()}},
                    "drillFilterOtherVisuals": True}}
    if title:
        v["visual"]["visualContainerObjects"] = titulo(title)
    return v


# --- Estilo IBCS: real en gris oscuro, presupuesto en gris claro, color solo para desviaciones
GRIS_AC, GRIS_PL, VERDE, ROJO, TINTA, SUAVE = "#3A3A3A", "#B5B5B5", "#3B9A52", "#D6453D", "#1A1A1A", "#6B6B6B"

TEMA = {
    "name": "Valdora IBCS",
    "dataColors": [GRIS_AC, GRIS_PL, "#7A7A7A", "#D9D9D9", "#5A5A5A", "#9A9A9A", VERDE, ROJO],
    "background": "#FFFFFF", "foreground": TINTA, "tableAccent": GRIS_AC,
    "good": VERDE, "bad": ROJO, "neutral": GRIS_PL,
    "textClasses": {
        "title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": TINTA},
        "header": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": TINTA},
        "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": SUAVE},
        "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 26, "color": TINTA},
    },
    "visualStyles": {"*": {"*": {
        "border": [{"show": False}],
        "dropShadow": [{"show": False}],
        "title": [{"fontSize": 12, "fontColor": {"solid": {"color": TINTA}}, "titleWrap": True}],
        "background": [{"show": False}],
    }}},
}


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def color(hexa):
    return {"solid": {"color": lit(f"'{hexa}'")}}


def tarjeta(nombre, medida, x, title, y=150):
    v = visual(nombre, "card", (x, y, 236, 96), {"Values": [campo("Medidas", medida, True)]}, title)
    v["visual"]["objects"] = {"labels": [{"properties": {"labelDisplayUnits": lit("1D"), "fontSize": lit("22D")}}],
                              "categoryLabels": [{"properties": {"show": lit("false")}}]}
    return v


def texto(nombre, pos, parrafos):
    """parrafos: lista de (texto, tamaño_pt, negrita, color)."""
    return {"$schema": f"{SCH}/definition/visualContainer/1.0.0/schema.json", "name": nombre,
            "position": {"x": pos[0], "y": pos[1], "z": 0, "width": pos[2], "height": pos[3]},
            "visual": {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [
                {"textRuns": [{"value": t, "textStyle": {"fontSize": f"{tam}pt", "fontWeight": "bold" if neg else "normal",
                                                          "color": col, "fontFamily": "Segoe UI"}}]}
                for t, tam, neg, col in parrafos]}}]}}}


def colores_series(v, mapa: dict):
    v["visual"].setdefault("objects", {})["dataPoint"] = [
        {"properties": {"fill": color(c)}, "selector": {"metadata": f"Medidas.{m}"}} for m, c in mapa.items()]
    return v


def color_condicional(v, medida_objetivo, medida_color):
    """Color de fuente por 'valor de campo'. El selector con dataViewWildcard lo aplica a cada celda
    (sin él, Power BI lo ignora): formato copiado de lo que guarda Power BI Desktop 2.157."""
    v["visual"].setdefault("objects", {}).setdefault("values", []).append({
        "properties": {"fontColor": {"solid": {"color": {"expr": {"Measure": {
            "Expression": {"SourceRef": {"Entity": "Medidas"}}, "Property": medida_color}}}}}},
        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}],
                     "metadata": f"Medidas.{medida_objetivo}"}})
    return v


def tabla_limpia(v, tam=9):
    """Tabla sobria: sin líneas verticales, filas compactas, encabezado en negrita."""
    o = v["visual"].setdefault("objects", {})
    o["grid"] = [{"properties": {"gridVertical": lit("false"), "gridHorizontal": lit("true"),
                                 "gridHorizontalColor": color("#E6E6E6"), "rowPadding": lit("2D"),
                                 "textSize": lit(f"{tam}D")}}]
    o["columnHeaders"] = [{"properties": {"fontSize": lit(f"{tam}D"), "bold": lit("true")}}]
    o.setdefault("values", []).insert(0, {"properties": {"fontSize": lit(f"{tam}D")}})
    return v


def ejes_limpios(v, tam_cat=9):
    """Sin títulos de eje (el título del gráfico ya lo dice) y valores sin escala automática ('mil MM')."""
    o = v["visual"].setdefault("objects", {})
    o["categoryAxis"] = [{"properties": {"showAxisTitle": lit("false"), "fontSize": lit(f"{tam_cat}D")}}]
    o["valueAxis"] = [{"properties": {"showAxisTitle": lit("false"), "labelDisplayUnits": lit("1D"),
                                      "fontSize": lit("9D")}}]
    return v


def etiquetas(v, tam=9):
    v["visual"].setdefault("objects", {})["labels"] = [{"properties": {
        "show": lit("true"), "labelDisplayUnits": lit("1D"), "fontSize": lit(f"{tam}D")}}]
    return v


def sin_encabezado(v):
    v["visual"].setdefault("objects", {})["header"] = [{"properties": {"show": lit("false")}}]  # el título del visual basta
    return v


def desplegable(v):
    v["visual"].setdefault("objects", {})["data"] = [{"properties": {"mode": lit("'Dropdown'")}}]
    return sin_encabezado(v)


def titular():
    """Mensaje clave: el mismo que genera exportar_dashboard.py para el tablero web."""
    ruta = PBI.parent / "docs" / "data.js"
    try:
        txt = ruta.read_text(encoding="utf-8")
        d = json.loads(txt[txt.index("=") + 1:].strip().rstrip(";"))
        c = d["cierre"]
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                 "noviembre", "diciembre"]
        brecha = c["proyeccion"] - c["ppto"]
        n = lambda v, dec=1: f"{v:,.{dec}f}".replace(",", "§").replace(".", ",").replace("§", ".")  # formato es-CL
        cierre = (f"Mes en curso ({meses[int(c['mes'][5:]) - 1]}, día {c['dia']} de {c['dias_mes']}): cierre proyectado de "
                  f"{n(c['proyeccion'] / c['ppto'] * 100)}% del presupuesto ({'−' if brecha < 0 else '+'}${n(abs(brecha), 0)} MM), "
                  f"con un error típico del método de {n(c['error'][0]['metodo'])}% al día 10.")
        return d["titular"], d["ytd_label"], cierre
    except Exception:
        return "Informe de control de gestión", "año a la fecha", ""


MENSAJE, PERIODO, CIERRE = titular()
ENCABEZADO = lambda pag: texto("encabezado", (20, 4, 1000, 66), [
    (f"TIENDAS VALDORA (EMPRESA FICTICIA) · {pag.upper()}", 9, True, SUAVE),
    (f"Informe de control de gestión · año a la fecha {PERIODO} · millones de pesos", 16, True, TINTA)])
SLICER_ZONA = lambda: desplegable(
    visual("slicerZona", "slicer", (1040, 8, 220, 62), {"Values": [campo("Tiendas", "zona")]}, "Zona"))

venta_mensual = ejes_limpios(colores_series(visual("gMensual", "lineClusteredColumnComboChart", (20, 260, 760, 250),
    {"Category": [campo("Calendario", "Periodo")], "Y": [campo("Medidas", "Venta Real (MM$)", True)],
     "Y2": [campo("Medidas", "Venta Ppto (MM$)", True)]},
    "Venta mensual (MM$): real (barras) vs presupuesto (línea)"), {"Venta Real (MM$)": GRIS_AC, "Venta Ppto (MM$)": GRIS_PL}), tam_cat=8)

desv_mensual = ejes_limpios(colores_series(visual("gDesv", "clusteredColumnChart", (20, 515, 760, 195),
    {"Category": [campo("Calendario", "Periodo")],
     "Y": [campo("Medidas", "Δ Favorable (MM$)", True), campo("Medidas", "Δ Desfavorable (MM$)", True)]},
    "Desviación mensual vs presupuesto (MM$)"), {"Δ Favorable (MM$)": VERDE, "Δ Desfavorable (MM$)": ROJO}), tam_cat=8)

pnl = tabla_limpia(color_condicional(color_condicional(visual("tPnl", "tableEx", (800, 260, 460, 450),
    {"Values": [campo("LineasPnL", "Linea", nombre=" "), campo("Medidas", "P&L Año Anterior", True, "Año ant."),
                campo("Medidas", "P&L Ppto", True, "Ppto"), campo("Medidas", "P&L Real", True, "Real"),
                campo("Medidas", "Δ Ppto %", True, "Δ vs Ppto"), campo("Medidas", "Δ AA %", True, "Δ vs AA")]},
    "Estado de resultados (MM$, año a la fecha)"), "Δ Ppto %", "Color Δ"), "Δ AA %", "Color Δ AA"))
pnl["visual"]["objects"]["total"] = [{"properties": {"totals": lit("false")}}]

cascada = etiquetas(ejes_limpios(visual("gCascada", "waterfallChart", (20, 80, 700, 330),
                 {"Category": [campo("PuenteEBITDA", "Concepto")], "Y": [campo("Medidas", "Paso Puente (MM$)", True)]},
                 "Puente de EBITDA: del presupuesto al real (MM$, año a la fecha)")))
cascada["visual"]["objects"]["sentimentColors"] = [{"properties": {
    "increaseFill": color(VERDE), "decreaseFill": color(ROJO), "totalFill": color(GRIS_AC)}}]

efectos = ejes_limpios(colores_series(visual("gEfectos", "clusteredBarChart", (740, 80, 520, 330),
    {"Category": [campo("Categorias", "categoria")],
     "Y": [campo("Medidas", "Efecto Precio YTD (MM$)", True), campo("Medidas", "Efecto Volumen y Mix YTD (MM$)", True)]},
    "Desviación de venta por categoría: precio vs volumen y mix (MM$)"),
    {"Efecto Precio YTD (MM$)": GRIS_AC, "Efecto Volumen y Mix YTD (MM$)": GRIS_PL}))

cumpl_cat = ejes_limpios(colores_series(visual("gCumplCat", "clusteredBarChart", (770, 425, 490, 285),
    {"Category": [campo("Categorias", "categoria")], "Y": [campo("Medidas", "Cumplimiento Venta YTD %", True)]},
    "Cumplimiento de venta por categoría (año a la fecha)"), {"Cumplimiento Venta YTD %": GRIS_AC}))
etiquetas(cumpl_cat)

tiendas = tabla_limpia(color_condicional(visual("tTiendas", "tableEx", (20, 425, 730, 285),
    {"Values": [campo("Tiendas", "tienda", nombre="Tienda"), campo("Tiendas", "zona", nombre="Zona"),
                campo("Tiendas", "formato", nombre="Formato"),
                campo("Medidas", "Venta YTD (MM$)", True, "Venta"), campo("Medidas", "Cumplimiento Venta YTD %", True, "Cumpl. venta"),
                campo("Medidas", "EBITDA YTD (MM$)", True, "EBITDA"), campo("Medidas", "Desv. EBITDA YTD (MM$)", True, "Δ EBITDA vs ppto"),
                campo("Medidas", "Cumplimiento EBITDA YTD %", True, "Cumpl. EBITDA")]},
    "Resultado por tienda (MM$, año a la fecha)"), "Desv. EBITDA YTD (MM$)", "Color Desv Tienda"), tam=8)

linea_tiempo = ejes_limpios(colores_series(visual("gTiempo", "lineClusteredColumnComboChart", (20, 190, 820, 260),
    {"Category": [campo("Calendario", "Periodo")], "Y": [campo("Medidas", "Venta Real (MM$)", True)],
     "Y2": [campo("Medidas", "Venta Ppto (MM$)", True), campo("Medidas", "Venta Año Ant. (MM$)", True)]},
    "Venta mensual (MM$): real (barras) vs presupuesto y año anterior (líneas)"),
    {"Venta Real (MM$)": GRIS_AC, "Venta Ppto (MM$)": GRIS_PL, "Venta Año Ant. (MM$)": "#7A7A7A"}), tam_cat=8)

donut = visual("gDonut", "donutChart", (860, 190, 400, 260),
               {"Category": [campo("Categorias", "categoria")], "Y": [campo("Medidas", "Venta YTD (MM$)", True)]},
               "Venta año a la fecha por categoría (MM$)")
donut["visual"]["objects"] = {"labels": [{"properties": {"labelStyle": lit("'Percent of total'"), "fontSize": lit("9D")}}],
                              "legend": [{"properties": {"position": lit("'Right'"), "fontSize": lit("9D")}}]}

zona_tienda = ejes_limpios(colores_series(visual("gZonaTienda", "clusteredBarChart", (20, 460, 620, 250),
    {"Category": [campo("Tiendas", "zona"), campo("Tiendas", "tienda")],
     "Y": [campo("Medidas", "Δ Favorable YTD (MM$)", True), campo("Medidas", "Δ Desfavorable YTD (MM$)", True)]},
    "Brecha vs presupuesto por zona (MM$, año a la fecha)"),
    {"Δ Favorable YTD (MM$)": VERDE, "Δ Desfavorable YTD (MM$)": ROJO}))

crec_mensual = etiquetas(ejes_limpios(colores_series(visual("gCrecAA", "clusteredColumnChart", (660, 460, 600, 250),
    {"Category": [campo("Calendario", "Periodo")], "Y": [campo("Medidas", "Crecimiento mensual vs AA %", True)]},
    "Crecimiento mensual de la venta vs año anterior"), {"Crecimiento mensual vs AA %": GRIS_AC}), tam_cat=8), tam=8)
crec_mensual["visual"]["objects"]["labels"][0]["properties"]["labelDisplayUnits"] = lit("0D")

PAGINAS = {
    "resumen": ("Resumen", [
        ENCABEZADO("Resumen ejecutivo"), SLICER_ZONA(),
        texto("mensaje", (20, 72, 1240, 82), [(MENSAJE, 14, True, TINTA), (CIERRE, 10, False, SUAVE)]),
        tarjeta("kVenta", "Venta YTD (MM$)", 20, "Venta"),
        tarjeta("kCumpl", "Cumplimiento Venta YTD %", 268, "Venta vs presupuesto"),
        tarjeta("kMargen", "Margen Real YTD %", 516, "Margen bruto"),
        tarjeta("kEbitda", "EBITDA YTD (MM$)", 764, "EBITDA"),
        tarjeta("kCumplEbitda", "Cumplimiento EBITDA YTD %", 1012, "EBITDA vs presupuesto"),
        venta_mensual, desv_mensual, pnl,
    ]),
    "ventas": ("Ventas", [
        ENCABEZADO("Ventas: real vs presupuesto y año anterior"), SLICER_ZONA(),
        tarjeta("vVenta", "Venta YTD (MM$)", 20, "Venta", y=76),
        tarjeta("vCumpl", "Cumplimiento Venta YTD %", 268, "Venta vs presupuesto", y=76),
        tarjeta("vDelta", "Δ Venta YTD vs Ppto (MM$)", 516, "Brecha vs presupuesto", y=76),
        tarjeta("vCrec", "Crecimiento YTD vs AA %", 764, "Crecimiento vs año anterior", y=76),
        linea_tiempo, donut, zona_tienda, crec_mensual,
    ]),
    "desviaciones": ("Desviaciones", [
        ENCABEZADO("¿Por qué nos desviamos?"), SLICER_ZONA(), cascada, efectos, tiendas, cumpl_cat,
    ]),
    "alertas": ("Alertas", [
        ENCABEZADO("Alertas automáticas"),
        texto("explica", (20, 70, 900, 50), [(
            "Reglas fijas: gasto atípico (presupuesto flexible + z-score robusto), venta bajo presupuesto 3 meses seguidos "
            "y precio/margen bajo presupuesto 2 meses seguidos. Recurrente = se repite en 2 de los últimos 3 meses.",
            10, False, SUAVE)]),
        sin_encabezado(visual("slicerTipo", "slicer", (940, 130, 320, 120), {"Values": [campo("Alertas", "tipo")]}, "Tipo de alerta")),
        ejes_limpios(colores_series(visual("gAlertasRegla", "clusteredBarChart", (940, 260, 320, 200),
            {"Category": [campo("Alertas", "regla_corta")], "Y": [campo("Medidas", "Alertas", True)]},
            "Alertas por regla"), {"Alertas": GRIS_AC})),
        ejes_limpios(colores_series(visual("gAlertasMes", "clusteredColumnChart", (940, 475, 320, 235),
            {"Category": [campo("Alertas", "periodo")], "Y": [campo("Medidas", "Alertas Recurrentes", True)]},
            "Alertas recurrentes por mes"), {"Alertas Recurrentes": ROJO})),
        tabla_limpia(visual("tAlertas", "tableEx", (20, 130, 900, 580),
               {"Values": [campo("Alertas", "periodo", nombre="Mes"), campo("Alertas", "regla", nombre="Regla"),
                           campo("Alertas", "tipo", nombre="Tipo"), campo("Alertas", "zona", nombre="Zona"),
                           campo("Alertas", "tienda", nombre="Tienda"), campo("Alertas", "categoria", nombre="Categoría"),
                           campo("Alertas", "detalle", nombre="Qué pasó"), campo("Alertas", "impacto_MM$", nombre="Impacto MM$")]},
               "Alertas del periodo"), tam=8),
    ]),
}


def generar_informe():
    d = RP / "definition"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    (RP / "definition.pbir").write_text(json.dumps({
        "$schema": f"{SCH}/definitionProperties/2.0.0/schema.json", "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{NOMBRE}.SemanticModel"}}}, indent=2), encoding="utf-8")
    (d / "version.json").write_text(json.dumps({
        "$schema": f"{SCH}/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}, indent=2), encoding="utf-8")
    (d / "report.json").write_text(json.dumps({
        "$schema": f"{SCH}/definition/report/1.0.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61", "type": "SharedResources"},
            "customTheme": {"name": "ValdoraIBCS.json", "reportVersionAtImport": "5.61", "type": "RegisteredResources"}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "ValdoraIBCS.json", "path": "ValdoraIBCS.json", "type": "CustomTheme"}]}],
        "layoutOptimization": "None"}, indent=2), encoding="utf-8")
    rr = RP / "StaticResources" / "RegisteredResources"
    rr.mkdir(parents=True, exist_ok=True)
    (rr / "ValdoraIBCS.json").write_text(json.dumps(TEMA, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "pages" / "pages.json").write_text(json.dumps({
        "$schema": f"{SCH}/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": list(PAGINAS), "activePageName": "resumen"}, indent=2), encoding="utf-8")
    for pid, (nombre, visuales) in PAGINAS.items():
        p = d / "pages" / pid
        (p / "visuals").mkdir(parents=True, exist_ok=True)
        (p / "page.json").write_text(json.dumps({
            "$schema": f"{SCH}/definition/page/1.0.0/schema.json", "name": pid, "displayName": nombre,
            "displayOption": "FitToPage", "height": 720, "width": 1280}, indent=2, ensure_ascii=False), encoding="utf-8")
        for v in visuales:
            (p / "visuals" / v["name"]).mkdir(exist_ok=True)
            (p / "visuals" / v["name"] / "visual.json").write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    for carpeta in (SM, RP):
        if carpeta.exists():
            shutil.rmtree(carpeta)
    generar_modelo()
    generar_informe()
    (PBI / f"{NOMBRE}.pbip").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NOMBRE}.Report"}}],
        "settings": {"enableAutoRecovery": True}}, indent=2), encoding="utf-8")
    print(f"Proyecto listo: {PBI / (NOMBRE + '.pbip')}  ({len(MEDIDAS)} medidas DAX, "
          f"{sum(len(v) for _, v in PAGINAS.values())} visuales en {len(PAGINAS)} páginas)")


if __name__ == "__main__":
    main()
