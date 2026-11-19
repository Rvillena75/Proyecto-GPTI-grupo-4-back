import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const [, , input, output, previewDir] = process.argv;
const evidence = JSON.parse(await fs.readFile(input, "utf8"));
const { corridas, fuentes, historico_original: historical } = evidence;
const book = Workbook.create();
const cases = ["sin", "con", "sin_estres", "con_estres"];
const labels = ["Normal sin StockSmart", "Normal con StockSmart", "Estrés sin StockSmart", "Estrés con StockSmart"];
const supplierNames = fuentes.proveedores.map((source) => source.supplier);
const views = [];
const kg = "#,##0.000";
const requiredKg = "#,##0.000000";
const clp = '"$"#,##0';
const pct = "0.00%";
const dateFormat = "dd/mm/yyyy";
const col = (index) => String.fromCharCode(65 + index);
const numeric = (value) => value === null ? null : Number(value);
const excelDate = (iso) => iso === null ? null : Date.parse(iso.slice(0, 10) + "T00:00:00Z") / 86400000 + 25569;
const formula = (sheet, cell, expression) => { sheet.getRange(cell).formulas = [[expression]]; };
const value = (sheet, cell, content) => { sheet.getRange(cell).values = [[content]]; };

function sheet(name, title, color, widths) {
  const tab = book.worksheets.add(name);
  tab.showGridLines = false;
  tab.tabColor = color;
  widths.forEach((width, i) => { tab.getRange(`${col(i)}:${col(i)}`).format.columnWidth = width; });
  value(tab, "A1", title);
  tab.getRange("A1").format.font.bold = true;
  tab.getRange("A1").format.font.size = 17;
  tab.getRange("A1").format.font.color = "#172C40";
  tab.getRange("A1").format.rowHeight = 28;
  return tab;
}

function block(tab, row, headers, data) {
  const last = col(headers.length - 1);
  const heading = tab.getRange(`A${row}:${last}${row}`);
  heading.values = [headers];
  heading.format.fill = "#243E54";
  heading.format.font.color = "#FFFFFF";
  heading.format.font.bold = true;
  heading.format.wrapText = true;
  heading.format.verticalAlignment = "center";
  heading.format.rowHeight = 34;
  const body = tab.getRange(`A${row+1}:${last}${row+data.length}`);
  body.values = data;
  body.format.rowHeight = 18;
  for (let i = 0; i < data.length; i += 2) {
    tab.getRange(`A${row+1+i}:${last}${row+1+i}`).format.fill = "#F1F5F8";
  }
  body.format.font.color = "#182B3A";
}

const summary = sheet("Resumen", "StockSmart  Resultados del protocolo E3", "#1F5F70",
  [27, 14, 15, 14, 16, 16, 19, 18, 17, 16, 16, 17, 16, 16]);
const params = sheet("Parametros", "Parámetros del protocolo aprobado", "#657F9A", [33, 22, 22, 22, 21, 24]);
block(params, 3, ["Proveedor", "Kg por formato", "Precio por formato CLP", "Despacho CLP", "Plazo días", "Captura publicada"],
  fuentes.proveedores.map((row) => [
    row.supplier, Number(row.pack_kg), Number(row.pack_clp), Number(row.shipping_clp),
    row.lead_days, excelDate(row.observation_source_at),
  ]));
params.getRange("B4:B6").format.numberFormat = kg;
params.getRange("C4:D6").format.numberFormat = clp;
params.getRange("F4:F6").format.numberFormat = dateFormat;
const assumptions = [
  ["Período", "27/07/2026 al 04/10/2026 inclusive. 70 días por corrida."],
  ["Stock inicial", "2,491 kg. Saldo de apertura ya existente."],
  ["Rotación", "Central → Jumbo → Santa Isabel. Una compra de 2,5 kg cada cinco días, con 5 / 5 / 4 pedidos."],
  ["Estrés", "Demanda × 1,40 del 14 al 20/09. Recepciones previstas del 15 al 17/09 se retrasan dos días."],
  ["Vencimiento", "68 días remanentes cerrado al recibir y cinco días desde el primer uso. FEFO."],
  ["Peso de Central", "2,5 kg por pieza nueva en ambas políticas y variantes."],
  ["Criterio de quiebre", "Demanda no atendida positiva. Cada día cuenta una vez."],
  ["Reposición", "Stock seguro y mayor plazo calculados por el backend. Objetivo de dos semanas y aprobación el mismo día."],
  ["Precios", "Capturas del 07/10. Importes constantes en el modelo del período. Cada despacho se cobra una vez."],
  ["Archivos de referencia", "Protocolo_Simulaciondocx.docx y Dataset_v2/dataset.json. Hashes y corridas en simulacion/corridas.json."],
  ["Reproducibilidad", "El Excel recalcula indicadores y alternativas. Para cambiar el abastecimiento, volver a ejecutar el backend."],
];
block(params, 9, ["Parámetro", "Regla", "", "", "", ""],
  assumptions.map(([label, rule]) => [label, rule, null, null, null, null]));
views.push(["Parametros", "A1:F20"]);

for (let index = 0; index < cases.length; index++) {
  const key = cases[index];
  const result = corridas[key];
  const daily = sheet(`Diario_${key}`, labels[index], "#8194A6",
    [15, 14, 14, 14, 14, 14, 14, 14, 12, 20, 15, 15, 15, 17, 21]);
  block(daily, 2, [
    "Fecha", "Apertura kg", "Recibido kg", "Demanda kg", "Atendida kg", "No atendida kg",
    "Vencidos kg", "Stock cierre kg", "Quiebre", "Estado consumo", "Promedio semanal kg",
    "Stock seguro kg", "Stock proyectado kg", "En tránsito antes kg", "Alerta antes de decidir",
  ], result.daily.map((row) => [
    excelDate(row.date), ...["opening_kg", "received_kg", "demand_kg", "fulfilled_kg"].map((k) => Number(row[k])),
    null, Number(row.waste_kg), null, null, row.consumption_status,
    ...["weekly_kg", "safe_kg", "projected_kg", "transit_kg_before_decision"].map((k) => numeric(row[k])),
    row.alert,
  ]));
  result.daily.forEach((_, index) => {
    const r = index + 3;
    formula(daily, `F${r}`, `=ROUND(D${r}-E${r},6)`);
    formula(daily, `H${r}`, `=ROUND(B${r}+C${r}-E${r}-G${r},6)`);
    formula(daily, `I${r}`, `=IF(F${r}>0,1,0)`);
  });
  daily.getRange("A3:A72").format.numberFormat = dateFormat;
  daily.getRange("B3:H72").format.numberFormat = kg;
  daily.getRange("K3:N72").format.numberFormat = kg;
  daily.freezePanes.freezeRows(2);
  views.push([daily.name, "A1:O6"]);

  const orders = sheet(`Ordenes_${key}`, labels[index] + "  Órdenes", "#8194A6",
    [24, 15, 15, 15, 15, 23, 13, 12, 17, 15, 15, 16, 15, 16, 22, 22, 22, 16, 15, 17, 16, 13, 18]);
  block(orders, 2, [
    "Orden", "Emisión", "Aprobación", "Recepción prevista", "Recepción real", "Proveedor",
    "Kg por formato", "Unidades", "Cantidad requerida (kg)", "Cantidad (kg)", "Excedente (kg)",
    "Subtotal CLP", "Despacho CLP", "Total elegido CLP", "Costo total si se compraba en Central",
    "Costo total si se compraba en Jumbo", "Costo total si se compraba en Santa Isabel",
    "Promedio 3 CLP", "Ahorro vs promedio", "Siguiente opción CLP", "Ahorro RN-11", "Atraso días",
    "Movimiento de entrada",
  ], result.orders.map((row) => [
    row.code, ...["issued", "approved", "planned", "received_at"].map((k) => excelDate(row[k])),
    row.supplier, Number(row.pack_kg), row.packs, Number(row.required_kg), Number(row.purchased_kg),
    null, Number(row.subtotal_clp), Number(row.shipping_clp), null, null, null, null, null, null, null,
    null, row.delay_days, row.receipt_movement_id,
  ]));
  result.orders.forEach((_, index) => {
    const r = index + 3;
    formula(orders, `K${r}`, `=ROUND(J${r}-I${r},6)`);
    formula(orders, `N${r}`, `=L${r}+M${r}`);
    ["O", "P", "Q"].forEach((c, offset) => {
      const p = offset + 4;
      formula(orders, `${c}${r}`,
        `=MAX(1,ROUNDUP(I${r}/Parametros!$B$${p},0))*Parametros!$C$${p}+Parametros!$D$${p}`);
    });
    formula(orders, `R${r}`, `=AVERAGE(O${r}:Q${r})`);
    if (key.startsWith("con")) {
      formula(orders, `S${r}`, `=(R${r}-N${r})/R${r}`);
      formula(orders, `T${r}`, `=SUM(O${r}:Q${r})-MIN(O${r}:Q${r})-MAX(O${r}:Q${r})`);
      formula(orders, `U${r}`, `=(T${r}-N${r})/T${r}`);
    }
  });
  const end = result.orders.length + 2;
  for (const [range, fmt] of [
    [`B3:E${end}`, dateFormat], [`G3:G${end}`, kg], [`I3:K${end}`, requiredKg],
    [`L3:R${end}`, clp], [`S3:S${end}`, pct], [`T3:T${end}`, clp], [`U3:U${end}`, pct],
  ]) orders.getRange(range).format.numberFormat = fmt;
  orders.getRange("A2:W2").format.rowHeight = 43;
  orders.freezePanes.freezeRows(2);
  views.push([orders.name, "A1:K6"], [orders.name, "L1:W6"]);
}

block(summary, 4, [
  "Caso", "Demanda kg", "Quiebres días", "No atendida kg", "Merma kg", "Stock final kg", "Pedidos",
  "Kg comprados", "Excedente kg", "Desembolso CLP", "Pedidos Central", "Pedidos Jumbo",
  "Pedidos Santa Isabel", "Pedidos atrasados",
], labels.map((label) => [label, ...Array(13).fill(null)]));
for (let index = 0; index < cases.length; index++) {
  const key = cases[index];
  const r = index + 5;
  const day = `Diario_${key}`;
  const order = `Ordenes_${key}`;
  const end = corridas[key].orders.length + 2;
  const values = [
    `SUM(${day}!D3:D72)`, `SUM(${day}!I3:I72)`, `SUM(${day}!F3:F72)`, `SUM(${day}!G3:G72)`,
    `${day}!H72`, `COUNTA(${order}!A3:A${end})`, `SUM(${order}!J3:J${end})`,
    `SUM(${order}!K3:K${end})`, `SUM(${order}!N3:N${end})`,
    ...supplierNames.map((name) => `COUNTIF(${order}!F3:F${end},"${name}")`),
    `COUNTIF(${order}!V3:V${end},">0")`,
  ];
  values.forEach((expression, i) => formula(summary, `${col(i+1)}${r}`, "=" + expression));
}
for (const range of ["B5:B8", "D5:F8", "H5:I8"]) summary.getRange(range).format.numberFormat = kg;
summary.getRange("J5:J8").format.numberFormat = clp;
block(summary, 11, ["Comparación", "Reducción quiebres", "Reducción merma", "Ahorro por orden", "Ahorro RN-11"],
  [["Normal", null, null, null, null], ["Estrés", null, null, null, null]]);
for (const [row, baseline, smart, key] of [[12, 5, 6, "con"], [13, 7, 8, "con_estres"]]) {
  const end = corridas[key].orders.length + 2;
  formula(summary, `B${row}`, `=IF(C${baseline}=0,"No medible",(C${baseline}-C${smart})/C${baseline})`);
  formula(summary, `C${row}`, `=IF(E${baseline}=0,"No medible",(E${baseline}-E${smart})/E${baseline})`);
  formula(summary, `D${row}`, `=AVERAGE(Ordenes_${key}!S3:S${end})`);
  formula(summary, `E${row}`, `=AVERAGE(Ordenes_${key}!U3:U${end})`);
}
summary.getRange("B12:E13").format.numberFormat = pct;
block(summary, 16, ["Meta oficial", "Escenario", "Umbral", "Resultado", "Evaluación"], [
  ["Reducir quiebres", "Estrés", 0.30, null, null],
  ["Reducir merma", "Normal", 0.20, null, null],
  ["Ahorrar por orden", "Normal", 0.05, null, null],
]);
for (const [r, source] of [[17, "B13"], [18, "C12"], [19, "D12"]]) {
  formula(summary, `D${r}`, `=${source}`);
  formula(summary, `E${r}`, `=IF(ISNUMBER(D${r}),IF(D${r}>=C${r},"Cumple","No cumple"),"No medible")`);
}
summary.getRange("C17:D19").format.numberFormat = pct;
value(summary, "A22", "Referencia histórica congelada");
summary.getRange("A22").format.font.bold = true;
value(summary, "A23", `Todos los pedidos a Central. 0 quiebres, ${Number(historical.expiry_merma).toFixed(3).replace(".", ",")} kg de merma y 14 entradas.`);
value(summary, "A25", "No medible indica que la base tuvo cero días con quiebre.");
value(summary, "A26", "El ahorro simulado compara alternativas para la misma necesidad bajo los parámetros del protocolo.");
views.unshift(["Resumen", "A1:N26"]);
book.recalculate();
const comparison = summary.getRange("B12:E13").values;
const official = summary.getRange("D17:E19").values;
const asNumber = (value) => Number(value);
const normal = evidence.indicadores.normal;
const stress = evidence.indicadores.estres;
const almost = (a, b) => Math.abs(a - asNumber(b)) < 1e-10;
if (comparison[0][0] !== "No medible" || comparison[1][0] !== "No medible" ||
    !almost(comparison[0][1], normal.merma_reduccion) || !almost(comparison[1][1], stress.merma_reduccion) ||
    !almost(comparison[0][2], normal.ahorro_orden) || !almost(comparison[1][2], stress.ahorro_orden) ||
    !almost(comparison[0][3], normal.ahorro_rn11) || !almost(comparison[1][3], stress.ahorro_rn11)) {
  throw new Error(`Excel y el backend discrepan: ${JSON.stringify(comparison)}`);
}
console.log(JSON.stringify({ comparison, official }));
const inspect = await book.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!",
  options: { useRegex: true, maxResults: 40 }, summary: "Escaneo de errores",
});
console.log(inspect.ndjson);
const file = await SpreadsheetFile.exportXlsx(book);
await file.save(output);
if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  for (const [i, [name, range]] of views.entries()) {
    const preview = await book.render({ sheetName: name, range, scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDir, `${i}-${name}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}
