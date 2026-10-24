// No cookie, no storage: the acceptance gate only lives in the page until it is closed.
const gate = document.getElementById("accept");
const gated = ["api-url", "tutorial", "try-box"].map((id) => document.getElementById(id));
gate.addEventListener("change", () => {
  for (const el of gated) {
    if (gate.checked) el.removeAttribute("hidden");
    else el.setAttribute("hidden", "");
  }
});

const out = document.getElementById("try-out");
document.getElementById("try-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = document.getElementById("try-text").value.trim();
  if (!text) return;
  out.textContent = "...";
  try {
    const r = await fetch("/api/v1/systemone", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        state: text,
        questions: {
          tier: { type: "choice", criteria: { TRIVIAL: "", LOW: "", MEDIUM: "", HIGH: "", EXPERT: "" } },
        },
      }),
    });
    const body = await r.text();
    try {
      out.textContent = JSON.stringify(JSON.parse(body), null, 2);
    } catch {
      out.textContent = `${r.status} ${body}`;
    }
    if (r.status === 429) out.textContent += "\nRate limit reached: retry in a few seconds.";
  } catch (e) {
    out.textContent = `Network error: ${e.message}`;
  }
});

fetch("/status.json")
  .then((r) => r.json())
  .then((s) => {
    for (const k of ["commit", "tantive_image", "model"]) {
      document.getElementById(`st-${k}`).textContent = s[k] || "unknown";
    }
  })
  .catch(() => {
    document.getElementById("st-model").textContent = "unavailable";
  });
