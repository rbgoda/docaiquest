// Google Drive Picker loader (M53 · "Import from Drive").
//
// drive.file scope can't list a user's Drive, but the Picker lets the user
// EXPLICITLY select a file — which grants the app drive.file access to just that
// file, so the backend can then fetch + ingest it. This module lazily loads the
// Google Picker JS and resolves with the picked file id (or null on cancel).

let _pickerLoaded = null;

function loadPicker() {
  if (_pickerLoaded) return _pickerLoaded;
  _pickerLoaded = new Promise((resolve, reject) => {
    if (window.google && window.google.picker) return resolve();
    const s = document.createElement("script");
    s.src = "https://apis.google.com/js/api.js";
    s.async = true;
    s.onload = () => {
      if (!window.gapi) return reject(new Error("gapi failed to load"));
      window.gapi.load("picker", { callback: resolve, onerror: () => reject(new Error("picker module failed")) });
    };
    s.onerror = () => reject(new Error("Could not load the Google Picker script"));
    document.body.appendChild(s);
  });
  return _pickerLoaded;
}

/**
 * Open the Drive Picker. Resolves with the selected file id, or null if cancelled.
 * @param {{accessToken:string, appId?:string, apiKey?:string}} cfg
 */
export async function openDrivePicker({ accessToken, appId, apiKey }) {
  await loadPicker();
  const g = window.google.picker;
  return new Promise((resolve, reject) => {
    if (!accessToken) return reject(new Error("Missing Drive access token"));
    const view = new g.DocsView(g.ViewId.DOCS)
      .setIncludeFolders(true)
      .setSelectFolderEnabled(false)
      .setMode(g.DocsViewMode.LIST);
    const builder = new g.PickerBuilder()
      .setOAuthToken(accessToken)
      .addView(view)
      .setTitle("Import a file from your Google Drive")
      .setCallback((data) => {
        if (data.action === g.Action.PICKED) {
          const doc = data.docs && data.docs[0];
          resolve(doc ? doc.id : null);
        } else if (data.action === g.Action.CANCEL) {
          resolve(null);
        }
      });
    if (appId) builder.setAppId(appId);
    if (apiKey) builder.setDeveloperKey(apiKey);
    builder.build().setVisible(true);
  });
}
