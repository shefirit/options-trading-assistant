/**
 * Trade logger for the Options Trading Assistant.
 *
 * HOW TO INSTALL (about 5 minutes, all inside your Google Sheet):
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
 * That's it. The app now writes every logged trade to the tab below.
 */

// The specific tab (worksheet) to write into. This is the gid from your sheet URL.
var TARGET_GID = 2063471337;

function doPost(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var data = JSON.parse(e.postData.contents);
    var header = data.header || [];
    var row = data.row || [];

    // Find the tab with the matching gid; fall back to the first tab.
    var sheet = null;
    var all = ss.getSheets();
    for (var i = 0; i < all.length; i++) {
      if (all[i].getSheetId() === TARGET_GID) { sheet = all[i]; break; }
    }
    if (!sheet) { sheet = ss.getSheets()[0]; }

    // Write a header row once, if the sheet is empty.
    if (sheet.getLastRow() === 0 && header.length > 0) {
      sheet.appendRow(header);
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

// Lets you confirm the web app is alive by opening the URL in a browser.
function doGet(e) {
  return ContentService
    .createTextOutput("Options Trading Assistant logger is running.")
    .setMimeType(ContentService.MimeType.TEXT);
}
