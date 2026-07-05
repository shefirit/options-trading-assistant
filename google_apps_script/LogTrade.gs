/**
 * Trade logger for the Options Trading Assistant.
 *
 * VERSION 5 - logs each trade two ways:
 *   1. A hidden, machine-readable tab ("Options Assistant Log") that powers the
 *      app's My trades screen (tracking, results, delete). Created automatically.
 *   2. A human row in your "App Trades" tab (a copy of your monthly M(1) sheet),
 *      in your own columns, with the profit/commission/bucket formulas filled in
 *      so it computes just like your teacher's format.
 *
 * If you had an older version, paste this whole file over it, then: Deploy ->
 * Manage deployments -> (pencil) Edit -> Version: New version -> Deploy. The web
 * app URL stays the same.
 */

// ---- the two tabs ----
var MACHINE_TAB = "Options Assistant Log";   // created automatically; app reads this
var MIRROR_TAB = "App Trades";               // your M(1)-format copy; must already exist

// ---- App Trades layout (1-based column numbers; header is on ROW 4) ----
var MIRROR_HEADER_ROW = 4;
var MIRROR_FIRST_ROW = 5;    // first trade row
var MIRROR_LAST_ROW = 16;    // last trade row (above your green totals row)
var COL = {
  TICKER: 1, CODE: 2, CALL_STRIKE: 3, PUT_STRIKE: 4, PREMIUM: 5, CONTRACTS: 6,
  PROFIT_PCT: 7, PROFIT: 8, COMMISSIONS: 9, BP: 10, PBP: 11,
  BUCKET_IC: 12, BUCKET_CS: 13, BUCKET_CC: 14, BUCKET_PMCC: 15, BUCKET_SP: 16,
  ROLL: 17, CLOSE: 18, TRADE_ID: 19, EXPIRATION: 20, DTE: 21, STATUS: 22
};

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function _machineSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(MACHINE_TAB);
  if (!sheet) { sheet = ss.insertSheet(MACHINE_TAB); }
  return sheet;
}

function _sheet() { return _machineSheet(); }   // doGet(mode=rows) reads this

function _mirrorSheet() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheetByName(MIRROR_TAB);  // null if absent
}

// ------------------------------------------------------------ POST
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);

    // Delete a trade everywhere (by Trade ID).
    if (data.action === "delete" && data.trade_id) {
      var removed = _deleteMachineRows(String(data.trade_id));
      _clearMirrorRow(String(data.trade_id));
      return _json({ ok: true, deleted: removed });
    }

    // Append the machine (tracking) row - unchanged behaviour.
    var header = data.header || [];
    var row = data.row || [];
    _appendMachineRow(header, row);

    // Mirror into the human App Trades tab.
    if (data.mirror) {
      if (data.mirror.close) {
        _updateMirrorClose(String(data.mirror.trade_id), Number(data.mirror.realized_pl));
      } else {
        _writeMirrorEntry(data.mirror);
      }
    }

    return _json({ ok: true, row: _machineSheet().getLastRow() });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

function _appendMachineRow(header, row) {
  var sheet = _machineSheet();
  if (sheet.getLastRow() === 0 && header.length > 0) {
    sheet.appendRow(header);
  } else if (header.length > 0 && sheet.getLastRow() > 0) {
    var have = sheet.getLastColumn();
    if (header.length > have) {
      sheet.getRange(1, have + 1, 1, header.length - have).setValues([header.slice(have)]);
    }
  }
  if (row.length > 0) { sheet.appendRow(row); }
}

function _deleteMachineRows(tradeId) {
  var sheet = _machineSheet();
  var last = sheet.getLastRow();
  if (last < 2) { return 0; }
  var values = sheet.getRange(1, 1, last, sheet.getLastColumn()).getValues();
  var idCol = values[0].indexOf("Trade ID");
  if (idCol < 0) { return 0; }
  var deleted = 0;
  for (var r = last; r >= 2; r--) {
    if (String(values[r - 1][idCol]) === tradeId) { sheet.deleteRow(r); deleted++; }
  }
  return deleted;
}

// ------------------------------------------------------------ App Trades mirror
// Guarded versions of ONLY the three formulas we confirmed from your sheet -
// same maths, but blank (not #VALUE! / #DIV/0!) when a row is empty. Your
// per-strategy bucket formulas (columns L-P) are left exactly as you made them.
//   H Profit$ = Profit% x Contracts x Premium   (=G*F*E)
//   I Commissions = Contracts x 2.6             (=F*4*0.65)
//   K P/BP = Profit$ / BP                        (=H/J)
function _mirrorFormulas(r) {
  return {
    8:  "=IF(OR(E" + r + "=\"\",F" + r + "=\"\"),\"\",G" + r + "*F" + r + "*E" + r + ")",
    9:  "=IF(F" + r + "=\"\",\"\",F" + r + "*4*0.65)",
    11: "=IF(OR(H" + r + "=\"\",J" + r + "=\"\",J" + r + "=0),\"\",H" + r + "/J" + r + ")"
  };
}

// Put the guarded formulas on every trade row, so empty rows stop showing errors.
function _repairMirrorFormulas(sheet) {
  for (var r = MIRROR_FIRST_ROW; r <= MIRROR_LAST_ROW; r++) {
    var f = _mirrorFormulas(r);
    for (var c in f) { sheet.getRange(r, Number(c)).setFormula(f[c]); }
  }
}

function _firstEmptyMirrorRow(sheet) {
  for (var r = MIRROR_FIRST_ROW; r <= MIRROR_LAST_ROW; r++) {
    if (sheet.getRange(r, COL.TICKER).getValue() === "") { return r; }
  }
  return -1;   // full
}

function _writeMirrorEntry(m) {
  var sheet = _mirrorSheet();
  if (!sheet) { return; }   // she hasn't made the App Trades tab yet
  _repairMirrorFormulas(sheet);
  var r = _firstEmptyMirrorRow(sheet);
  if (r < 0) { return; }    // no free row this month - leave totals untouched
  sheet.getRange(r, COL.TICKER).setValue(m.ticker || "");
  sheet.getRange(r, COL.CODE).setValue(m.code || "");
  sheet.getRange(r, COL.CALL_STRIKE).setValue(m.call_strike === undefined ? "" : m.call_strike);
  sheet.getRange(r, COL.PUT_STRIKE).setValue(m.put_strike === undefined ? "" : m.put_strike);
  sheet.getRange(r, COL.PREMIUM).setValue(m.premium || 0);
  sheet.getRange(r, COL.CONTRACTS).setValue(m.contracts || 0);
  sheet.getRange(r, COL.PROFIT_PCT).setValue(m.profit_pct === undefined ? 1 : m.profit_pct);
  sheet.getRange(r, COL.BP).setValue(m.bp || 0);
  sheet.getRange(r, COL.TRADE_ID).setValue(m.trade_id || "");
  sheet.getRange(r, COL.EXPIRATION).setValue(m.expiration || "");
  sheet.getRange(r, COL.DTE).setValue(m.dte === undefined ? "" : m.dte);
  sheet.getRange(r, COL.STATUS).setValue("open");
}

function _findMirrorRow(sheet, tradeId) {
  for (var r = MIRROR_FIRST_ROW; r <= MIRROR_LAST_ROW; r++) {
    if (String(sheet.getRange(r, COL.TRADE_ID).getValue()) === tradeId) { return r; }
  }
  return -1;
}

// On close: set Profit% so Profit$ (=G*F*E) shows the realized result, mark CLOSE.
function _updateMirrorClose(tradeId, realizedPl) {
  var sheet = _mirrorSheet();
  if (!sheet) { return; }
  var r = _findMirrorRow(sheet, tradeId);
  if (r < 0) { return; }
  var premium = Number(sheet.getRange(r, COL.PREMIUM).getValue()) || 0;
  var contracts = Number(sheet.getRange(r, COL.CONTRACTS).getValue()) || 0;
  var maxCredit = premium * contracts;
  var pct = maxCredit ? (realizedPl / maxCredit) : 0;
  sheet.getRange(r, COL.PROFIT_PCT).setValue(pct);
  sheet.getRange(r, COL.CLOSE).setValue("YES");
  sheet.getRange(r, COL.STATUS).setValue("closed");
}

// On delete: blank the trade's values, leave the guarded formulas so the row is
// a clean empty formula row again (never touches your totals or plan).
function _clearMirrorRow(tradeId) {
  var sheet = _mirrorSheet();
  if (!sheet) { return; }
  var r = _findMirrorRow(sheet, tradeId);
  if (r < 0) { return; }
  var valueCols = [COL.TICKER, COL.CODE, COL.CALL_STRIKE, COL.PUT_STRIKE, COL.PREMIUM,
                   COL.CONTRACTS, COL.PROFIT_PCT, COL.BP, COL.ROLL, COL.CLOSE,
                   COL.TRADE_ID, COL.EXPIRATION, COL.DTE, COL.STATUS];
  for (var i = 0; i < valueCols.length; i++) {
    sheet.getRange(r, valueCols[i]).clearContent();
  }
  var f = _mirrorFormulas(r);
  for (var c in f) { sheet.getRange(r, Number(c)).setFormula(f[c]); }
}

// ------------------------------------------------------------ GET
// mode=rows -> the machine tab as JSON (used by My trades).
function doGet(e) {
  try {
    if (e && e.parameter && e.parameter.mode === "rows") {
      var sheet = _sheet();
      var last = sheet.getLastRow();
      var header = [], rows = [];
      if (last >= 1) {
        var values = sheet.getRange(1, 1, last, sheet.getLastColumn()).getValues();
        header = values[0];
        rows = values.slice(1);
      }
      return _json({ ok: true, header: header, rows: rows });
    }
    return ContentService
      .createTextOutput("Options Trading Assistant logger is running (v5).")
      .setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}
