/**
 * Trade logger for the Options Trading Assistant.
 *
 * VERSION 3 - adds read-back (My trades tab) AND deleting a trade you logged by
 * mistake or while testing. If you installed an older version, paste this whole
 * file over it, then: Deploy -> Manage deployments -> (pencil icon) Edit ->
 * Version: New version -> Deploy. The web app URL stays the same.
 *
 * HOW TO INSTALL FROM SCRATCH (about 5 minutes, all inside your Google Sheet):
 *   1. Open your Google Sheet.
 *   2. Menu:  Extensions  ->  Apps Script.
 *   3. Delete anything in the editor, then paste ALL of this file in.
 *   4. Click  Save  (the disk icon).
 *   5. Click  Deploy  ->  New deployment.
 *   6. Click the gear next to "Select type" and choose  Web app.
 *   7. Set "Who has access" to  Anyone.  Click  Deploy.
 *   8. Approve the permissions when Google asks (it is your own script).
 *   9. Copy the  Web app URL  it shows you.
 *  10. Paste that URL into the app: sidebar -> "Connect Google Sheet" -> Save.
 *
 * Note: "Anyone" means anyone who has this exact long URL can write to and
 * read this one tab. Keep the URL private, like a password.
 */

// The specific tab (worksheet) to write into. This is the gid from your sheet URL.
var TARGET_GID = 2063471337;

function _sheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var all = ss.getSheets();
  for (var i = 0; i < all.length; i++) {
    if (all[i].getSheetId() === TARGET_GID) { return all[i]; }
  }
  return all[0];
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var sheet = _sheet();

    // Delete every row belonging to one trade (by its Trade ID).
    if (data.action === "delete" && data.trade_id) {
      var last = sheet.getLastRow();
      if (last < 2) {
        return _json({ ok: true, deleted: 0 });
      }
      var values = sheet.getRange(1, 1, last, sheet.getLastColumn()).getValues();
      var idCol = values[0].indexOf("Trade ID");
      if (idCol < 0) {
        return _json({ ok: false, error: "No 'Trade ID' column found." });
      }
      var deleted = 0;
      for (var r = last; r >= 2; r--) {   // bottom-up so row numbers stay valid
        if (String(values[r - 1][idCol]) === String(data.trade_id)) {
          sheet.deleteRow(r);
          deleted++;
        }
      }
      return _json({ ok: true, deleted: deleted });
    }

    var header = data.header || [];
    var row = data.row || [];

    // Write a header row once, if the sheet is empty.
    if (sheet.getLastRow() === 0 && header.length > 0) {
      sheet.appendRow(header);
    } else if (header.length > 0 && sheet.getLastRow() > 0) {
      // Newer app versions add tracker columns (Trade ID, Event...). Extend
      // the header labels once so the new cells have names.
      var have = sheet.getLastColumn();
      if (header.length > have) {
        sheet.getRange(1, have + 1, 1, header.length - have)
             .setValues([header.slice(have)]);
      }
    }
    sheet.appendRow(row);

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, row: sheet.getLastRow() }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// mode=rows -> the whole log as JSON (used by the "My trades" tab).
// Anything else -> a plain "alive" message you can check in a browser.
function doGet(e) {
  try {
    if (e && e.parameter && e.parameter.mode === "rows") {
      var sheet = _sheet();
      var last = sheet.getLastRow();
      var header = [];
      var rows = [];
      if (last >= 1) {
        var values = sheet.getRange(1, 1, last, sheet.getLastColumn()).getValues();
        header = values[0];
        rows = values.slice(1);
      }
      return ContentService
        .createTextOutput(JSON.stringify({ ok: true, header: header, rows: rows }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    return ContentService
      .createTextOutput("Options Trading Assistant logger is running (v3).")
      .setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
