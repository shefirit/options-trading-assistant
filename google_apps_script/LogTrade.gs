/**
 * Trade logger for the Options Trading Assistant.
 *
 * VERSION 9 - also copies every REAL trade into your TWA tracker spreadsheet
 * (one strike per row). Nothing in this log changes; the tracker is a second
 * copy. The first time you deploy this, Google asks you to allow the script to
 * open that other spreadsheet - say Allow.
 *
 * VERSION 8 - real money and practice money live in SEPARATE TABS.
 *
 *   "Real Money Log"       every trade placed with real money
 *   "Practice Log"         every trade placed in thinkorswim PaperMoney
 *   "Options Assistant Log"  your original tab: read-only history from before
 *                            the two books were split. Nothing is ever written
 *                            to it again, and everything in it is practice -
 *                            it all predates the day you funded the account.
 *
 * Both new tabs are created automatically the first time a trade of that kind
 * is logged. NOTHING in your existing tab is moved, renamed or deleted.
 *
 * Which tab a row goes to is decided by its "Account" column, and when the app
 * reads back, the TAB is what decides which book a trade belongs to - so a row
 * cannot be in the wrong book even if that cell is edited by hand.
 *
 * If you had an older version, paste this whole file over it, then: Deploy ->
 * Manage deployments -> (pencil) Edit -> Version: New version -> Deploy. The web
 * app URL stays the same.
 *
 * NOTE (2026-07-14): the App Trades mirror is RETIRED - Rita moved to the app's
 * English month-by-month tracking, so the app no longer sends the "mirror"
 * field and doPost's mirror branch simply never runs. The code stays for
 * compatibility. The App Trades tab is a frozen archive.
 */

// ---- the trade books ----
var REAL_TAB = "Real Money Log";             // created automatically
var PAPER_TAB = "Practice Log";              // created automatically
var LEGACY_TAB = "Options Assistant Log";    // read-only history, never written
var MIRROR_TAB = "App Trades";               // frozen archive of the M(1) format

var ACCOUNT_COL_NAME = "Account";

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

// The tab a trade of this account belongs in, created on first use.
function _bookSheet(account) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var name = (account === "real") ? REAL_TAB : PAPER_TAB;
  var sheet = ss.getSheetByName(name);
  if (!sheet) { sheet = ss.insertSheet(name); }
  return sheet;
}

// Every tab the app reads, each with the book it belongs to. The tab decides
// the book - that is what "completely separate" means here.
function _allBooks() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var out = [];
  var wanted = [[REAL_TAB, "real"], [PAPER_TAB, "paper"], [LEGACY_TAB, "paper"]];
  for (var i = 0; i < wanted.length; i++) {
    var sheet = ss.getSheetByName(wanted[i][0]);
    if (sheet) { out.push({ sheet: sheet, account: wanted[i][1] }); }
  }
  return out;
}

function _mirrorSheet() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheetByName(MIRROR_TAB);  // null if absent
}

// ------------------------------------------------------------ POST
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);

    // Bring trades' rows in the TWA tracker up to date (v9).
    if (data.action === "tracker") {
      return _json({ ok: true, tracker: _syncTracker(data.trades || []) });
    }

    // Delete a trade everywhere (by Trade ID), whichever book it is in.
    if (data.action === "delete" && data.trade_id) {
      var removed = _deleteMachineRows(String(data.trade_id));
      _clearMirrorRow(String(data.trade_id));
      return _json({ ok: true, deleted: removed });
    }

    var header = data.header || [];
    var row = data.row || [];
    var sheet = _appendMachineRow(header, row);

    // Mirror into the human App Trades tab (retired - the app stopped sending
    // this field, so this branch no longer runs).
    if (data.mirror) {
      if (data.mirror.close) {
        _updateMirrorClose(String(data.mirror.trade_id), Number(data.mirror.realized_pl));
      } else {
        _writeMirrorEntry(data.mirror);
      }
    }

    return _json({ ok: true, tab: sheet.getName(), row: sheet.getLastRow() });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

// Read the row's Account cell to decide which book it belongs in. Anything
// that is not exactly "real" goes to practice: counting a practice trade as
// real income is the one mistake this must never make, so "not sure" means
// practice.
function _accountFromRow(header, row) {
  var i = header.indexOf(ACCOUNT_COL_NAME);
  if (i < 0 || i >= row.length) { return "paper"; }
  return (String(row[i]).trim().toLowerCase() === "real") ? "real" : "paper";
}

function _appendMachineRow(header, row) {
  var account = _accountFromRow(header, row);
  var sheet = _bookSheet(account);
  if (sheet.getLastRow() === 0 && header.length > 0) {
    sheet.appendRow(header);
  } else if (header.length > 0 && sheet.getLastRow() > 0) {
    var have = sheet.getLastColumn();
    if (header.length > have) {
      sheet.getRange(1, have + 1, 1, header.length - have).setValues([header.slice(have)]);
    }
  }
  if (row.length > 0) { sheet.appendRow(row); }
  return sheet;
}

// Delete every row of this trade, in whichever book holds it. The legacy tab is
// included so old trades can still be removed from the app.
function _deleteMachineRows(tradeId) {
  var books = _allBooks();
  var deleted = 0;
  for (var b = 0; b < books.length; b++) {
    var sheet = books[b].sheet;
    var last = sheet.getLastRow();
    if (last < 2) { continue; }
    var values = sheet.getRange(1, 1, last, sheet.getLastColumn()).getValues();
    var idCol = values[0].indexOf("Trade ID");
    if (idCol < 0) { continue; }
    for (var r = last; r >= 2; r--) {
      if (String(values[r - 1][idCol]) === tradeId) { sheet.deleteRow(r); deleted++; }
    }
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

// The value cells the app owns (everything except the guarded formula columns).
var _MIRROR_VALUE_COLS = [
  1, 2, 3, 4, 5, 6, 7, 10, 17, 18, 19, 20, 21, 22   // A-G, J, ROLL, CLOSE, tracking S-V
];

// Turn one row into a clean, empty formula row: clear its values (removing any
// leftover strategy code / text "100%" that made #VALUE!) and set the guarded
// formulas, so an unused row shows blank instead of an error.
function _resetMirrorRow(sheet, r) {
  for (var i = 0; i < _MIRROR_VALUE_COLS.length; i++) {
    sheet.getRange(r, _MIRROR_VALUE_COLS[i]).clearContent();
  }
  var f = _mirrorFormulas(r);
  for (var c in f) { sheet.getRange(r, Number(c)).setFormula(f[c]); }
}

// The last trade row = the row just ABOVE your green totals row, found by looking
// for the SUM formula in the totals row's bucket column. This adapts if rows get
// added/deleted, so the script never overwrites your totals row.
function _lastDataRow(sheet) {
  for (var r = MIRROR_FIRST_ROW; r <= MIRROR_FIRST_ROW + 100; r++) {
    var f = sheet.getRange(r, COL.BUCKET_SP).getFormula();
    if (f && f.toUpperCase().indexOf("SUM") >= 0) { return r - 1; }
  }
  return MIRROR_LAST_ROW;   // fallback if no totals row is found
}

// A row is "free" when it has no Trade ID - i.e. it isn't a logged app trade.
function _firstFreeMirrorRow(sheet) {
  var last = _lastDataRow(sheet);
  for (var r = MIRROR_FIRST_ROW; r <= last; r++) {
    if (sheet.getRange(r, COL.TRADE_ID).getValue() === "") { return r; }
  }
  return -1;   // full
}

function _writeMirrorEntry(m) {
  var sheet = _mirrorSheet();
  if (!sheet) { return; }   // she hasn't made the App Trades tab yet
  var last = _lastDataRow(sheet);
  // Blank every trade row that isn't a logged app trade (no Trade ID). This wipes
  // leftover M(1) sample rows that showed #VALUE! and never touches the totals row.
  for (var rr = MIRROR_FIRST_ROW; rr <= last; rr++) {
    if (sheet.getRange(rr, COL.TRADE_ID).getValue() === "") { _resetMirrorRow(sheet, rr); }
  }
  var r = _firstFreeMirrorRow(sheet);
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
  var last = _lastDataRow(sheet);
  for (var r = MIRROR_FIRST_ROW; r <= last; r++) {
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

// On delete: blank the trade's row back to a clean empty formula row (never
// touches your totals or plan).
function _clearMirrorRow(tradeId) {
  var sheet = _mirrorSheet();
  if (!sheet) { return; }
  var r = _findMirrorRow(sheet, tradeId);
  if (r < 0) { return; }
  _resetMirrorRow(sheet, r);
}

// ------------------------------------------------------------ TWA tracker
// Your copy of the TWA Discord Tracker. The app works out every row (it
// replays the trade - a roll closes one row and opens the next); this part
// only finds the right row and writes it.
//
// The rules that keep your template safe:
//   - a cell holding a FORMULA is never written (P/L, days, return, price...)
//   - rows are found by the "App ID" column, added to the right of your notes
//     the first time; your own rows (no App ID) are never touched
//   - the SAMPLE row is typed over first, as the template asks
//   - your Notes on a row are never overwritten once the row exists
var TRACKER_ID = "1Gmk_dE6nXo_lHzqF8WlRKc9Qu050EQq68sRtsQ8CEUQ";
var TRACKER_GID = 2074716553;
var TRACKER_KEY = "App ID";
var TRACKER_DATES = { "Open Date": 1, "Exp Date": 1, "Closed Date": 1 };
// Formula columns the template means to have on every row. On a row the app
// writes, an EMPTY cell here gets the formula copied from your own template
// row (references shift to the row, as a paste would). A cell that already
// has a formula, or anything typed, is left alone.
var TRACKER_FILL = ["Today's Price", "Profit Breach Alert"];
// The template's tabs name a few columns differently.
var TRACKER_ALIASES = {
  "Closed Date": ["Closed Date", "Close Date", "Date Closed"],
  "Open Date": ["Open Date", "Open"],
  "Premium $": ["Premium $", "Fill $"],
  "Collateral": ["Collateral"]
};
var SAMPLE_TEXT = "This first row is a SAMPLE";

var _trackerTz = "";   // the tracker spreadsheet's own time zone

function _trackerSheet() {
  var ss = SpreadsheetApp.openById(TRACKER_ID);
  _trackerTz = ss.getSpreadsheetTimeZone();
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (sheets[i].getSheetId() === TRACKER_GID) { return sheets[i]; }
  }
  throw new Error("The TWA tracker tab was not found (gid " + TRACKER_GID + ").");
}

// Where the header row is and which column each name lives in. Never by a
// fixed row number - you may add rows above it.
function _trackerLayout(sheet) {
  var lastCol = sheet.getLastColumn();
  var top = sheet.getRange(1, 1, Math.min(10, sheet.getLastRow()), lastCol).getValues();
  for (var r = 0; r < top.length; r++) {
    var names = top[r].map(function (v) { return String(v).trim(); });
    if (names.indexOf("Type") < 0 || names.indexOf("Ticker") < 0) { continue; }
    var cols = {};
    var lastNamed = 0;
    for (var c = 0; c < names.length; c++) {
      if (!names[c]) { continue; }
      lastNamed = c + 1;
      if (!(names[c] in cols)) { cols[names[c]] = c + 1; }
      if (names[c].indexOf("Notes") === 0 && !cols["Notes"]) { cols["Notes"] = c + 1; }
    }
    // The SAMPLE tab's notes column has no header: it is the one right after
    // "Annual Return Rate".
    if (!cols["Notes"] && cols["Annual Return Rate"]) {
      cols["Notes"] = cols["Annual Return Rate"] + 1;
    }
    if (!cols[TRACKER_KEY]) {
      var at = Math.max(lastNamed, cols["Notes"] || 0) + 1;
      sheet.getRange(r + 1, at).setValue(TRACKER_KEY);
      cols[TRACKER_KEY] = at;
    }
    return { headerRow: r + 1, cols: cols };
  }
  throw new Error("Could not find the header row (Type ... Ticker) in the TWA tracker tab.");
}

function _trackerCol(cols, name) {
  var names = TRACKER_ALIASES[name] || [name];
  for (var i = 0; i < names.length; i++) {
    if (cols[names[i]]) { return cols[names[i]]; }
  }
  return 0;
}

// Midnight in the TRACKER's time zone. A plain new Date() is midnight in the
// script's zone, which shows as the day before in a sheet set further west.
function _isoToDate(s) {
  return Utilities.parseDate(String(s), _trackerTz || Session.getScriptTimeZone(), "yyyy-MM-dd");
}

// Write {col: value} into row r, skipping formula cells, in as few calls as
// possible (one per run of side-by-side columns).
function _writeCells(sheet, r, writes) {
  var lastCol = sheet.getLastColumn();
  var formulas = sheet.getRange(r, 1, 1, lastCol).getFormulas()[0];
  var cols = Object.keys(writes).map(Number).filter(function (c) {
    return !(c <= formulas.length && formulas[c - 1]);
  }).sort(function (a, b) { return a - b; });
  var i = 0;
  while (i < cols.length) {
    var j = i;
    while (j + 1 < cols.length && cols[j + 1] === cols[j] + 1) { j++; }
    var vals = [];
    for (var k = i; k <= j; k++) { vals.push(writes[cols[k]]); }
    sheet.getRange(r, cols[i], 1, vals.length).setValues([vals]);
    i = j + 1;
  }
}

// The app's notes are lines joined by " / " (one per roll). Returns the cell's
// text with any line it lacks added on the end, or null when nothing changes.
var NOTE_SEP = " / ";
function _mergeNotes(have, want) {
  want = String(want || "");
  if (!have) { return want ? want : null; }
  var out = have;
  var parts = want.split(NOTE_SEP);
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i].trim();
    if (part && out.indexOf(part) < 0) { out += NOTE_SEP + part; }
  }
  return out === have ? null : out;
}

function _fillFormulas(sheet, r, templates) {
  for (var col in templates) {
    var src = templates[col];
    if (src === r) { continue; }
    var target = sheet.getRange(r, Number(col));
    if (target.getFormula() || String(target.getValue()) !== "") { continue; }
    sheet.getRange(src, Number(col)).copyTo(
      target, SpreadsheetApp.CopyPasteType.PASTE_FORMULA, false);
  }
}

function _syncTracker(trades) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var sheet = _trackerSheet();
    var layout = _trackerLayout(sheet);
    var cols = layout.cols;
    var first = layout.headerRow + 1;
    var lastRow = Math.max(sheet.getLastRow(), first);
    var n = lastRow - first + 1;
    var lastCol = sheet.getLastColumn();
    var grid = sheet.getRange(first, 1, n, lastCol).getValues();
    var keyAt = cols[TRACKER_KEY] - 1, typeAt = cols["Type"] - 1,
        tickerAt = cols["Ticker"] - 1, notesAt = (cols["Notes"] || 0) - 1;

    function cell(i, at) { return at >= 0 && at < grid[i].length ? String(grid[i][at]).trim() : ""; }
    function isSample(i) { return notesAt >= 0 && cell(i, notesAt).indexOf(SAMPLE_TEXT) === 0; }

    // Your dropdowns. A few rows of the template allow fewer types than the
    // rest (row 23's Strategy has no LEAP), and Google refuses a value its
    // dropdown does not list - so a new row only goes where it is allowed.
    var openAt = _trackerCol(cols, "Open Date") - 1;

    // Where each per-row formula can be copied from: the first row of your
    // template that has it. Your template has Today's Price only on row 3 and
    // from row 53 down, so rows in between would otherwise show no price.
    var templates = {};
    TRACKER_FILL.forEach(function (name) {
      var col = cols[name];
      if (!col) { return; }
      var f = sheet.getRange(first, col, n, 1).getFormulas();
      for (var x = 0; x < f.length; x++) {
        if (f[x][0]) { templates[col] = first + x; break; }
      }
    });

    var rules = {};
    ["Type", "Strategy"].forEach(function (name) {
      rules[name] = sheet.getRange(first, cols[name], n, 1).getDataValidations();
    });
    function allowed(i, name, value) {
      var rule = (rules[name] && i < rules[name].length) ? rules[name][i][0] : null;
      if (!rule || rule.getAllowInvalid() || value === "" || value === undefined) { return true; }
      if (rule.getCriteriaType() !== SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST) { return true; }
      var list = rule.getCriteriaValues()[0] || [];
      var want = String(value).toLowerCase();
      for (var x = 0; x < list.length; x++) {
        if (String(list[x]).trim().toLowerCase() === want) { return true; }
      }
      return false;
    }
    function fits(i, row) {
      return allowed(i, "Type", row.cells["Type"]) && allowed(i, "Strategy", row.cells["Strategy"]);
    }

    // Every name the app writes, so a cleared row is cleared completely.
    var names = ["Type", "Strategy", "Open Date", "Exp Date", "Closed Date", "Ticker",
                 "Strike $", "Quantity", "Premium $", "Close $",
                 "Covered Call Collateral", "Collateral", "Notes"];
    var written = 0, cleared = 0, errors = [];

    for (var t = 0; t < trades.length; t++) {
      var tradeId = String(trades[t].trade_id || "");
      if (!tradeId) { continue; }
      var rows = trades[t].rows || [];
      var wanted = {};
      for (var w = 0; w < rows.length; w++) { wanted[rows[w].key] = rows[w]; }

      // Rows this trade already has.
      var have = {};
      for (var i = 0; i < grid.length; i++) {
        var k = cell(i, keyAt);
        if (k.indexOf(tradeId + "#") === 0) { have[k] = i; }
      }

      // Rows it should no longer have (a deleted trade, a corrected roll).
      for (var gone in have) {
        if (wanted[gone]) { continue; }
        var clear = {};
        for (var c = 0; c < names.length; c++) {
          var col = _trackerCol(cols, names[c]);
          if (col) { clear[col] = ""; }
        }
        clear[cols[TRACKER_KEY]] = "";
        _writeCells(sheet, first + have[gone], clear);
        // The breach-alert formula the app copied in reads FALSE on an empty
        // row, so it goes with the row - except on the row it is copied FROM.
        var alertCol = cols["Profit Breach Alert"];
        if (alertCol && templates[alertCol] !== first + have[gone]) {
          sheet.getRange(first + have[gone], alertCol).clearContent();
        }
        grid[have[gone]][keyAt] = "";
        grid[have[gone]][typeAt] = "";
        grid[have[gone]][tickerAt] = "";
        cleared++;
      }

      for (var w2 = 0; w2 < rows.length; w2++) {
        var row = rows[w2];
        var at = have[row.key];
        var isNew = (at === undefined);
        var overSample = false;
        if (isNew) {
          at = -1;
          for (var i2 = 0; i2 < grid.length; i2++) {
            if (cell(i2, keyAt) || !fits(i2, row)) { continue; }
            // Free = no ticker and no open date. A dropdown picked on its own
            // is not a trade (and the first, failed backfill left one).
            if (isSample(i2) || (!cell(i2, tickerAt) && !cell(i2, openAt))) {
              at = i2; overSample = isSample(i2); break;
            }
          }
          if (at < 0) {   // no free row left: add one under the last
            grid.push(new Array(lastCol).fill(""));
            at = grid.length - 1;
          }
        }
        var writes = {};
        for (var name in row.cells) {
          var col2 = _trackerCol(cols, name);
          if (!col2) { continue; }
          var v = row.cells[name];
          // Your notes are yours: the app only ADDS the lines a cell does not
          // already have (a new roll), after whatever you wrote.
          if (name === "Notes" && !overSample) {
            v = _mergeNotes(cell(at, notesAt), v);
            if (v === null) { continue; }
          }
          if (TRACKER_DATES[name] && v) { v = _isoToDate(v); }
          writes[col2] = v;
        }
        // The App ID goes in first, on its own: if Google then refuses a cell,
        // the row is still claimed and the next copy finishes it, instead of
        // leaving half a row nobody owns.
        grid[at][keyAt] = row.key;
        grid[at][typeAt] = row.cells["Type"] || "x";
        grid[at][tickerAt] = row.cells["Ticker"] || "x";
        if (notesAt >= 0 && overSample) { grid[at][notesAt] = row.cells["Notes"] || ""; }
        try {
          sheet.getRange(first + at, cols[TRACKER_KEY]).setValue(row.key);
          _writeCells(sheet, first + at, writes);
          _fillFormulas(sheet, first + at, templates);
          written++;
        } catch (err) {
          errors.push(row.key + " (row " + (first + at) + "): " + String(err));
        }
      }
    }
    return { written: written, cleared: cleared, errors: errors, tab: sheet.getName() };
  } finally {
    lock.releaseLock();
  }
}

// ------------------------------------------------------------ GET
// mode=rows -> every book as one JSON table (used by My trades).
//
// The rows come back merged, each one carrying the Account of the TAB it was
// read from. That is deliberate: the tab is the record of which book a trade is
// in, so editing the Account cell by hand cannot move a trade between books.
function doGet(e) {
  try {
    if (e && e.parameter && e.parameter.mode === "rows") {
      var books = _allBooks();
      var header = [];
      var tables = [];

      // Read every tab first, so the widest header wins - the legacy tab is one
      // column narrower than the two new ones.
      for (var b = 0; b < books.length; b++) {
        var sheet = books[b].sheet;
        var last = sheet.getLastRow();
        if (last < 1) { continue; }
        var values = sheet.getRange(1, 1, last, sheet.getLastColumn()).getValues();
        if (values[0].length > header.length) { header = values[0]; }
        tables.push({ head: values[0], rows: values.slice(1), account: books[b].account });
      }
      if (header.indexOf(ACCOUNT_COL_NAME) < 0) { header = header.concat([ACCOUNT_COL_NAME]); }
      var acctAt = header.indexOf(ACCOUNT_COL_NAME);

      // Map each tab's rows onto the shared header BY COLUMN NAME, then stamp
      // the account from the tab it came from.
      var rows = [];
      for (var t = 0; t < tables.length; t++) {
        var tbl = tables[t];
        for (var r = 0; r < tbl.rows.length; r++) {
          var src = tbl.rows[r];
          if (String(src.join("")).trim() === "") { continue; }   // blank row
          var out = [];
          for (var c = 0; c < header.length; c++) {
            var at = tbl.head.indexOf(header[c]);
            out.push((at >= 0 && at < src.length) ? src[at] : "");
          }
          out[acctAt] = tbl.account;
          rows.push(out);
        }
      }
      return _json({ ok: true, header: header, rows: rows });
    }
    return ContentService
      .createTextOutput("Options Trading Assistant logger is running (v9 - "
                        + "real money and practice in separate tabs, TWA tracker copy).")
      .setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}
