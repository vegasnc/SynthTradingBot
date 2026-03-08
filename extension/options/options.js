import { getEngineUrl, setEngineUrl } from "../lib/storage.js";

const input = document.getElementById("engineUrl");
const saveBtn = document.getElementById("save");
const status = document.getElementById("status");

getEngineUrl().then((url) => {
  input.value = url;
});

saveBtn.addEventListener("click", async () => {
  const url = input.value.trim();
  if (!url) {
    status.textContent = "URL cannot be empty.";
    return;
  }
  await setEngineUrl(url);
  status.textContent = "Saved.";
  setTimeout(() => (status.textContent = ""), 2000);
});
