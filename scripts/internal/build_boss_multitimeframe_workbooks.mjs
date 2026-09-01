import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = process.argv[2];
if (!root) throw new Error("usage: node build_boss_multitimeframe_workbooks.mjs <result-root>");
const previewRoot = path.join(root, "workbook_previews");
await fs.mkdir(previewRoot, { recursive: true });

function excelColumn(columnCount) {
  let value = columnCount;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

const imports = [
  ["boss_multitimeframe_overview.csv", "Overview"],
  ["boss_multitimeframe_strategy_summary.csv", "By_Strategy"],
  ["boss_multitimeframe_by_symbol.csv", "By_Symbol"],
  ["boss_multitimeframe_by_timeframe.csv", "By_Timeframe"],
  ["boss_multitimeframe_execution_wait.csv", "Execution_Wait"],
  ["persistent_position_candidates.csv", "Persistent_Position"],
  ["persistence_parameter_audit.csv", "Parameter_Persistence"],
  ["reference_position_behavior.csv", "Reference_ZIP_Behavior"],
  ["boss_multitimeframe_tick_master.csv", "All_Cases"],
];

const firstText = await fs.readFile(path.join(root, imports[0][0]), "utf8");
const workbook = await Workbook.fromCSV(firstText, { sheetName: imports[0][1] });
for (const [file, sheetName] of imports.slice(1)) {
  const csvText = await fs.readFile(path.join(root, file), "utf8");
  await workbook.fromCSV(csvText, { sheetName });
}

const headerFill = "#17365D";
for (const [, sheetName] of imports) {
  const sheet = workbook.worksheets.getItem(sheetName);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const used = sheet.getUsedRange(true);
  if (!used) continue;
  const values = used.values;
  const rowCount = values.length;
  const colCount = values[0]?.length ?? 0;
  if (!rowCount || !colCount) continue;
  const header = sheet.getRangeByIndexes(0, 0, 1, colCount);
  header.format = {
    fill: headerFill,
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#8EA9C1" },
  };
  header.format.rowHeightPx = 34;
  used.format.font = { name: "Aptos", size: 10 };
  used.format.autofitColumns();
  for (let col = 0; col < colCount; col += 1) {
    const label = String(values[0][col] ?? "");
    const column = sheet.getRangeByIndexes(1, col, Math.max(1, rowCount - 1), 1);
    if (/Return|MDD|fraction|median_nonflat|median_flat/i.test(label)) {
      column.format.numberFormat = "0.00%;[Red](0.00%);-";
    } else if (/BE_bps/i.test(label)) {
      column.format.numberFormat = "0.00;[Red](0.00);-";
    } else if (/Turnover_raw|median_turnover/i.test(label)) {
      column.format.numberFormat = "0.00x;[Red](0.00x);-";
    } else if (/Turnover_pct/i.test(label)) {
      column.format.numberFormat = "0.0\"%\";[Red](0.0\"%\");-";
    } else if (/duration|seconds|wait_/i.test(label)) {
      column.format.numberFormat = "#,##0.0;[Red](#,##0.0);-";
    }
    const width = /reason|path|member|parameter/i.test(label) ? 220 : Math.min(150, Math.max(80, label.length * 7));
    column.format.columnWidthPx = width;
  }
  if (rowCount > 1) {
    sheet.getRangeByIndexes(1, 0, rowCount - 1, colCount).format.borders = {
      insideHorizontal: { style: "thin", color: "#E5E7EB" },
    };
  }
}

const overview = workbook.worksheets.getItem("Overview");
overview.getRange("A1:B1").format = {
  fill: "#0B1F33", font: { bold: true, color: "#FFFFFF", size: 12 },
};
overview.getRange("A1:A10").format.font = { bold: true, color: "#17365D" };
overview.getRange("A1:A10").format.columnWidthPx = 220;
overview.getRange("B1:B10").format.columnWidthPx = 160;

const timeframeSheet = workbook.worksheets.getItem("By_Timeframe");
const chart = timeframeSheet.charts.add("bar", timeframeSheet.getRange("A1:C5"));
chart.title = "Cases and Positive Return Cases by Signal Timeframe";
chart.hasLegend = true;
chart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 10 } };
chart.yAxis = { numberFormatCode: "#,##0" };
chart.setPosition("K2", "R20");

const checks = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
if (checks.ndjson && /#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/.test(checks.ndjson)) {
  throw new Error(`workbook formula error scan failed: ${checks.ndjson}`);
}
const reviewInspection = await workbook.inspect({
  kind: "table",
  range: "Overview!A1:B20",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 8,
});
if (!reviewInspection.ndjson) throw new Error("review workbook inspection returned no data");
for (const [, sheetName] of imports) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const values = sheet.getUsedRange(true)?.values ?? [[""]];
  const rows = Math.max(1, Math.min(25, values.length));
  const cols = Math.max(1, Math.min(12, values[0]?.length ?? 1));
  const preview = await workbook.render({
    sheetName,
    range: `A1:${excelColumn(cols)}${rows}`,
    scale: 1,
    format: "png",
  });
  const previewBytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(
    path.join(previewRoot, `${sheetName}.png`),
    previewBytes,
  );
  if (sheetName === "Overview") {
    await fs.writeFile(
      path.join(root, "boss_multitimeframe_tick_review_preview.png"),
      previewBytes,
    );
  }
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(root, "boss_multitimeframe_tick_review.xlsx"));

const candidateText = await fs.readFile(path.join(root, "boss_multitimeframe_candidates.csv"), "utf8");
const candidateWorkbook = await Workbook.fromCSV(candidateText, { sheetName: "Candidates" });
const candidateSheet = candidateWorkbook.worksheets.getItem("Candidates");
candidateSheet.showGridLines = false;
candidateSheet.freezePanes.freezeRows(1);
const candidateUsed = candidateSheet.getUsedRange(true);
const candidateValues = candidateUsed.values;
const candidateCols = candidateValues[0].length;
candidateSheet.getRangeByIndexes(0, 0, 1, candidateCols).format = {
  fill: headerFill, font: { bold: true, color: "#FFFFFF" }, wrapText: true,
};
candidateUsed.format.autofitColumns();
const candidateHeaders = candidateValues[0].map(String);
for (let col = 0; col < candidateCols; col += 1) {
  const label = candidateHeaders[col];
  const column = candidateSheet.getRangeByIndexes(1, col, Math.max(1, candidateValues.length - 1), 1);
  if (/Return|fraction/i.test(label)) column.format.numberFormat = "0.00%;[Red](0.00%);-";
  if (/BE_bps/i.test(label)) column.format.numberFormat = "0.00;[Red](0.00);-";
  if (/Turnover_raw/i.test(label)) column.format.numberFormat = "0.00x;[Red](0.00x);-";
  column.format.columnWidthPx = /why_shortlisted/i.test(label) ? 220 : Math.min(150, Math.max(80, label.length * 7));
}
const candidateInspection = await candidateWorkbook.inspect({
  kind: "table",
  range: "Candidates!A1:L25",
  include: "values,formulas",
  tableMaxRows: 25,
  tableMaxCols: 12,
});
if (!candidateInspection.ndjson) throw new Error("candidate workbook inspection returned no data");
const candidateErrors = await candidateWorkbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "candidate formula error scan",
});
if (candidateErrors.ndjson && /#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/.test(candidateErrors.ndjson)) {
  throw new Error(`candidate workbook formula error scan failed: ${candidateErrors.ndjson}`);
}
const candidatePreview = await candidateWorkbook.render({
  sheetName: "Candidates",
  range: `A1:${excelColumn(Math.min(12, candidateCols))}${Math.min(25, candidateValues.length)}`,
  scale: 1,
  format: "png",
});
await fs.writeFile(
  path.join(previewRoot, "Candidates.png"),
  new Uint8Array(await candidatePreview.arrayBuffer()),
);
const candidateOutput = await SpreadsheetFile.exportXlsx(candidateWorkbook);
await candidateOutput.save(path.join(root, "boss_multitimeframe_candidates.xlsx"));

console.log(JSON.stringify({
  review: path.join(root, "boss_multitimeframe_tick_review.xlsx"),
  candidates: path.join(root, "boss_multitimeframe_candidates.xlsx"),
  sheets: imports.map((item) => item[1]),
}));
