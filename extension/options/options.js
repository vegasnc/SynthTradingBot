import { getEngineUrl, setEngineUrl } from "../lib/storage.js";

const input = document.getElementById("engineUrl");
const saveBtn = document.getElementById("save");
const status = document.getElementById("status");

getEngineUrl().then((url) => {
  input.value = url;
});

saveBtn.addEventListener("click", async () => {
  const url = input.value.trim();
  status.classList.remove("success", "error");
  if (!url) {
    status.textContent = "URL cannot be empty.";
    status.classList.add("error");
    return;
  }
  await setEngineUrl(url);
  status.textContent = "Saved.";
  status.classList.add("success");
  setTimeout(() => {
    status.textContent = "";
    status.classList.remove("success");
  }, 2000);
});
